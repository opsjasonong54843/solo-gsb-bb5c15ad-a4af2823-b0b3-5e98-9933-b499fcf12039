"""单向抓包负载重组核心逻辑。

输入为若干乱序分片（起始偏移 + 原始字节），按字节位置重组：
  * 同一字节位置多次覆盖且取值相同 -> 去重；
  * 同一字节位置取值不同         -> 标记为冲突；
  * 从未被覆盖的字节             -> 计入缺口区间。

重组结果以行程（run）列表表示 [start, end, value?]，end 为开区间端点。
"""

from __future__ import annotations

from typing import Any

# 每个冲突位置最多保留几个不同取值（用于页面展示，防止异常输入撑爆内存）
SAMPLE_VALUES_PER_POSITION = 8
# 最多抽样多少个冲突位置
SAMPLE_POSITION_LIMIT = 2000
# 覆盖区预览最多返回多少字节
PREVIEW_LIMIT = 512


class ReassemblyError(ValueError):
    """可直接作为 400 返回给前端的重组错误。"""


def parse_hex_payload(raw: str, index: int) -> bytes:
    """解析用户填写的十六进制内容，允许空白与冒号分隔，要求偶数个 hex 位。"""
    if not isinstance(raw, str):
        raise ReassemblyError(f"分片 {index + 1} 的内容必须是十六进制字符串")
    text = raw.replace(":", "").replace("-", "").replace("\n", "").replace("\r", "")
    text = "".join(text.split())
    if text == "":
        raise ReassemblyError(f"分片 {index + 1} 的内容为空")
    if len(text) % 2 != 0:
        raise ReassemblyError(
            f"分片 {index + 1} 的十六进制长度为奇数（{len(text)}），无法按字节解析"
        )
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ReassemblyError(f"分片 {index + 1} 含非法十六进制字符：{exc}") from None


def invert_runs(runs: list[list[int]], total: int) -> list[list[int]]:
    """求区间列表在 [0, total) 内的补集（即全部缺口区间）。"""
    gaps: list[list[int]] = []
    cursor = 0
    for start, end in runs:
        if start > cursor:
            gaps.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < total:
        gaps.append([cursor, total])
    return gaps


def assemble(
    expected_length: int,
    fragments: list[dict[str, Any]],
    *,
    max_fragments: int,
    max_total_input: int,
) -> tuple[dict[str, Any], bytes | None]:
    """执行重组。

    返回 (报告字典, 可下载字节)。
    只有范围完整（无缺口）且无冲突时，第二项才是完整字节串，否则为 None。
    """
    if isinstance(expected_length, bool) or not isinstance(expected_length, int):
        raise ReassemblyError("预期字节数必须是非负整数")
    if expected_length < 0:
        raise ReassemblyError("预期字节数不能为负数")
    if not isinstance(fragments, list) or not fragments:
        raise ReassemblyError("至少需要提交一个分片")
    if len(fragments) > max_fragments:
        raise ReassemblyError(f"分片数量 {len(fragments)} 超过上限 {max_fragments}")

    parsed: list[tuple[int, bytes]] = []
    total_input = 0
    last_end = 0
    in_order = True
    for i, frag in enumerate(fragments):
        if not isinstance(frag, dict):
            raise ReassemblyError(f"分片 {i + 1} 格式不合法")
        offset = frag.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ReassemblyError(f"分片 {i + 1} 的起始偏移必须是非负整数")
        if offset < 0:
            raise ReassemblyError(f"分片 {i + 1} 的起始偏移不能为负数")
        payload = parse_hex_payload(frag.get("data", ""), i)
        length = len(payload)
        if length == 0:
            raise ReassemblyError(f"分片 {i + 1} 的长度为 0")
        if offset + length > expected_length:
            raise ReassemblyError(
                f"分片 {i + 1}（偏移 {offset}，长度 {length}）超出预期长度 "
                f"{expected_length}，覆盖范围 [{offset}, {offset + length}) 越界"
            )
        total_input += length
        if total_input > max_total_input:
            raise ReassemblyError(
                f"所有分片的字节总量 {total_input} 超过上限 {max_total_input}"
            )
        if offset < last_end:
            in_order = False
        last_end = max(last_end, offset + length)
        parsed.append((offset, payload))

    data = bytearray(expected_length)
    covered = bytearray(expected_length)  # 0/1 掩码
    conflict = bytearray(expected_length)  # 0/1 掩码
    # 冲突位置 -> 收到过的不同取值（限幅，仅用于展示）
    value_sets: dict[int, list[int]] = {}
    dup_bytes = 0  # 同值重叠字节数
    conflict_bytes = 0  # 异值重叠写入次数（按写入计）

    for offset, payload in parsed:
        for j, value in enumerate(payload):
            pos = offset + j
            if not covered[pos]:
                covered[pos] = 1
                data[pos] = value
                continue
            if data[pos] == value and not conflict[pos]:
                # 与已采纳字节同值：去重
                dup_bytes += 1
                continue
            # 异值
            if not conflict[pos]:
                conflict[pos] = 1
                vs = [data[pos], value]
                value_sets[pos] = vs[:SAMPLE_VALUES_PER_POSITION]
            else:
                vs = value_sets.get(pos)
                if vs is not None and value not in vs and len(vs) < SAMPLE_VALUES_PER_POSITION:
                    vs.append(value)
            conflict_bytes += 1

    # 由 0/1 掩码生成行程区间（逐字节扫描，上限受 expected_length 配置约束）
    coverage_runs = _mask_to_runs(covered)
    conflict_runs = _mask_to_runs(conflict)
    gap_runs = invert_runs(coverage_runs, expected_length)

    covered_length = sum(e - s for s, e in coverage_runs)
    conflict_length = sum(e - s for s, e in conflict_runs)
    complete = covered_length == expected_length
    has_conflict = conflict_length > 0
    downloadable = complete and not has_conflict
    payload_out: bytes | None = bytes(data) if downloadable else None

    conflict_samples = [
        {"offset": pos, "values": vs}
        for pos, vs in sorted(value_sets.items())[:SAMPLE_POSITION_LIMIT]
    ]

    preview_hex = None
    preview_truncated = False
    if expected_length:
        preview_len = min(expected_length, PREVIEW_LIMIT)
        preview_hex = bytes(data[:preview_len]).hex()
        preview_truncated = expected_length > preview_len

    fragment_report = [
        {
            "index": i + 1,
            "offset": off,
            "length": len(pay),
            "end": off + len(pay),
        }
        for i, (off, pay) in enumerate(parsed)
    ]

    report: dict[str, Any] = {
        "expected_length": expected_length,
        "complete": complete,
        "has_conflict": has_conflict,
        "downloadable": downloadable,
        "in_order": in_order,
        "stats": {
            "fragment_count": len(parsed),
            "total_input_bytes": total_input,
            "covered_length": covered_length,
            "gap_length": expected_length - covered_length,
            "conflict_length": conflict_length,
            "duplicate_bytes": dup_bytes,
            "conflict_write_count": conflict_bytes,
        },
        "coverage": coverage_runs,
        "conflicts": conflict_runs,
        "gaps": gap_runs,
        "conflict_samples": conflict_samples,
        "conflict_samples_truncated": len(value_sets) > len(conflict_samples),
        "preview_hex": preview_hex,
        "preview_truncated": preview_truncated,
        "fragments": fragment_report,
    }
    return report, payload_out


def _mask_to_runs(mask: bytearray) -> list[list[int]]:
    """把 0/1 掩码压缩为 [start, end) 行程列表。"""
    runs: list[list[int]] = []
    n = len(mask)
    i = 0
    while i < n:
        if mask[i] == 0:
            i += 1
            continue
        start = i
        end = mask.find(0, i + 1)
        if end == -1:
            runs.append([start, n])
            break
        runs.append([start, end])
        i = end + 1
    return runs
