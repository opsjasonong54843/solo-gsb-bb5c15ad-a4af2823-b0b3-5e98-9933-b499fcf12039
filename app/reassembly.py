"""按字节偏移重组单向抓包分片。

规则:
- 各分片按 ``offset`` 定位,逐字节覆盖到 [0, expected_length) 区间;
- 重叠位置若值相同则去重,值不同则该字节标记为冲突;
- 缺口 = [0, expected_length) 中从未被任何分片覆盖的区间;
- 只有 "范围完整且无冲突" 时才允许还原二进制下载。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 单个区间预览的最大字节数,避免响应体过大
PREVIEW_BYTES = 32
# 每个冲突区间保留的现场样本数量上限
MAX_CONFLICT_SAMPLES = 32
# 各区间列表的最大条目数(超出部分截断并在响应中标记)
MAX_RANGES_RETURNED = 500

_HEX_CLEAN_RE = re.compile(r"[^0-9a-fA-F]")
_HEX_PREFIX_RE = re.compile(r"0[xX]")


class ReassemblyError(ValueError):
    """输入不合法(偏移、十六进制内容等)。"""


class ReassemblyLimitError(ReassemblyError):
    """超出服务端允许的处理上限。"""


@dataclass
class Fragment:
    offset: int
    data: bytes
    label: str = ""


def parse_hex(raw: str, *, max_chars: int | None = None, index: int | None = None) -> bytes:
    """解析用户输入的十六进制内容。

    接受 ``0x`` 前缀、空白以及 ``-: 0x`` 等常见分隔符;
    必须包含偶数个十六进制数字,否则报错。
    """
    if raw is None:
        raise ReassemblyError(_where(index) + "十六进制内容为空")
    stripped = _HEX_PREFIX_RE.sub("", raw)
    cleaned = _HEX_CLEAN_RE.sub("", stripped)
    if not cleaned:
        raise ReassemblyError(_where(index) + "十六进制内容为空")
    if len(cleaned) % 2 != 0:
        raise ReassemblyError(
            _where(index) + f"十六进制数字个数必须为偶数,实际为 {len(cleaned)} 个"
        )
    if max_chars is not None and len(cleaned) > max_chars:
        raise ReassemblyLimitError(
            _where(index)
            + f"十六进制内容长度 {len(raw)} 字符超过上限 {max_chars} 字符"
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:  # pragma: no cover - 清洗后理论上不会触发
        raise ReassemblyError(_where(index) + "十六进制内容无法解析") from exc


def _where(index: int | None) -> str:
    return f"第 {index + 1} 个分片:" if index is not None else ""


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并相交/相邻的 [start, end) 半开区间。"""
    if not spans:
        return []
    spans = sorted(spans)
    merged: list[list[int]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _invert(spans: list[tuple[int, int]], end: int) -> list[tuple[int, int]]:
    """取 [0, end) 内 spans 覆盖范围的补集。spans 必须已合并。"""
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, finish in spans:
        if start > cursor:
            gaps.append((cursor, start))
        if finish > cursor:
            cursor = finish
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def _runs(flags: bytearray, want: bool, upto: int) -> list[tuple[int, int]]:
    """从布尔标记数组中提取所有值为 want 的连续区间。"""
    out: list[tuple[int, int]] = []
    start: int | None = None
    target = 1 if want else 0
    for i in range(upto):
        if flags[i] == target:
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, upto))
    return out


def _preview(
    data: bytes | None, start: int, end: int, conflicting: bytearray | None
) -> str:
    """生成区间前 ``PREVIEW_BYTES`` 字节的十六进制预览。"""
    if data is None:
        return ""
    piece = data[start : min(end, start + PREVIEW_BYTES)]
    return piece.hex(" ")


def _serialize_ranges(
    spans: list[tuple[int, int]],
    *,
    data: bytes | None = None,
    conflicting: bytearray | None = None,
    sources: dict[int, list[str]] | None = None,
    samples: dict[int, list[str]] | None = None,
) -> tuple[list[dict], bool]:
    ranges: list[dict] = []
    truncated = False
    for n, (start, end) in enumerate(spans):
        if n >= MAX_RANGES_RETURNED:
            truncated = True
            break
        item: dict = {"start": start, "end": end, "length": end - start}
        if data is not None:
            item["hex_preview"] = _preview(data, start, end, conflicting)
        if conflicting is not None:
            item["conflict_bytes"] = sum(
                1 for i in range(start, end) if conflicting[i]
            )
        if sources is not None:
            item["sources"] = sources.get(start, [])
        if samples is not None:
            item["samples"] = samples.get(start, [])
        ranges.append(item)
    return ranges, truncated


def reassemble(
    expected_length: int,
    fragments: list[Fragment],
    *,
    max_expected: int,
    max_total_bytes: int,
    max_fragments: int,
    max_hex_chars: int,
) -> dict:
    """重组分片并返回覆盖/冲突/缺口等完整结果。"""
    if not isinstance(expected_length, int) or expected_length < 0:
        raise ReassemblyError("预期字节数必须是非负整数")
    if expected_length > max_expected:
        raise ReassemblyLimitError(f"预期字节数超过上限 {max_expected}")
    if len(fragments) > max_fragments:
        raise ReassemblyLimitError(f"分片数量超过上限 {max_fragments}")

    total = sum(len(f.data) for f in fragments)
    if total > max_total_bytes:
        raise ReassemblyLimitError(
            f"全部分片合计 {total} 字节,超过上限 {max_total_bytes} 字节"
        )

    first_value = bytearray(expected_length)   # 该偏移第一次出现的字节值
    covered = bytearray(expected_length)       # 1 = 已覆盖
    conflicting = bytearray(expected_length)   # 1 = 出现过异值重叠
    conflict_values: dict[int, set[str]] = {}  # 偏移 -> 出现过的不同值
    overlap_spans: list[tuple[int, int]] = []  # 超出预期长度的部分(溢出)

    for frag in fragments:
        if frag.offset < 0:
            raise ReassemblyError(f"分片 {frag.label or ''} 的起始偏移不能为负")
        f_start = frag.offset
        f_end = frag.offset + len(frag.data)
        # 落在 [0, expected_length) 之外的尾部视为溢出,不参与重组
        if f_end > expected_length:
            over_start = max(f_start, expected_length)
            if over_start < f_end:
                overlap_spans.append((over_start, f_end))

        for pos in range(f_start, min(f_end, expected_length)):
            val = frag.data[pos - f_start]
            if not covered[pos]:
                covered[pos] = 1
                first_value[pos] = val
            elif first_value[pos] != val:
                conflicting[pos] = 1
                bucket = conflict_values.get(pos)
                if bucket is None and len(conflict_values) < MAX_CONFLICT_SAMPLES:
                    bucket = {f"{first_value[pos]:02x}"}
                    conflict_values[pos] = bucket
                if bucket is not None and len(bucket) < 8:
                    bucket.add(f"{val:02x}")

    covered_spans = merge_spans(_runs(covered, True, expected_length))
    conflict_spans = merge_spans(_runs(conflicting, True, expected_length))
    gap_spans = _invert(covered_spans, expected_length)
    overflow_spans = merge_spans(overlap_spans)

    # 每个冲突区间上有哪些分片经过(便于定位是哪几片打架)
    conflict_sources: dict[int, list[str]] = {}
    for c_start, c_end in conflict_spans:
        names: list[str] = []
        for idx, frag in enumerate(fragments):
            f_start = frag.offset
            f_end = frag.offset + len(frag.data)
            if f_start < c_end and f_end > c_start:
                names.append(frag.label or f"分片 {idx + 1}")
        if len(conflict_spans) <= MAX_RANGES_RETURNED:
            conflict_sources[c_start] = names[:16]

    # 每个冲突区间内抽取若干字节的不同取值现场
    conflict_samples: dict[int, list[str]] = {}
    if len(conflict_spans) <= MAX_RANGES_RETURNED:
        for c_start, c_end in conflict_spans:
            picked = 0
            for pos in range(c_start, c_end):
                values = conflict_values.get(pos)
                if values:
                    conflict_samples.setdefault(c_start, []).append(
                        f"偏移 {pos}: {' / '.join(sorted(values))}"
                    )
                    picked += 1
                    if picked >= 4:
                        break

    payload = bytes(first_value)

    covered_ranges, covered_truncated = _serialize_ranges(
        covered_spans, data=payload
    )
    conflict_ranges, conflict_truncated = _serialize_ranges(
        conflict_spans,
        data=payload,
        conflicting=conflicting,
        sources=conflict_sources,
        samples=conflict_samples,
    )
    gap_ranges, gap_truncated = _serialize_ranges(gap_spans)
    overflow_ranges, overflow_truncated = _serialize_ranges(overflow_spans)

    covered_bytes = sum(covered)
    conflict_bytes = sum(conflicting)
    gap_bytes = expected_length - covered_bytes
    clean_bytes = covered_bytes - conflict_bytes
    complete = (
        expected_length > 0
        and gap_bytes == 0
        and conflict_bytes == 0
    )

    return {
        "expected_length": expected_length,
        "stats": {
            "fragment_count": len(fragments),
            "submitted_bytes": total,
            "covered_bytes": covered_bytes,
            "conflict_bytes": conflict_bytes,
            "clean_bytes": clean_bytes,
            "gap_bytes": gap_bytes,
            "coverage_percent": round(
                covered_bytes * 100 / expected_length, 2
            ) if expected_length else 0.0,
        },
        "ranges": {
            "covered": covered_ranges,
            "conflict": conflict_ranges,
            "gap": gap_ranges,
            "overflow": overflow_ranges,
            "truncated": (
                covered_truncated
                or conflict_truncated
                or gap_truncated
                or overflow_truncated
            ),
            "max_ranges": MAX_RANGES_RETURNED,
        },
        "complete": complete,
        "can_download": complete,
        "payload_hex": payload.hex() if complete else None,
        "limits": {
            "max_expected": max_expected,
            "max_total_bytes": max_total_bytes,
            "max_fragments": max_fragments,
        },
    }
