"""FastAPI 入口:静态页面 + 重组接口 + 二进制下载接口。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .reassembly import (
    Fragment,
    ReassemblyError,
    ReassemblyLimitError,
    parse_hex,
    reassemble,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_EXPECTED = int(os.getenv("MAX_EXPECTED_BYTES", str(16 * 1024 * 1024)))
MAX_TOTAL_BYTES = int(os.getenv("MAX_TOTAL_BYTES", str(16 * 1024 * 1024)))
MAX_FRAGMENTS = int(os.getenv("MAX_FRAGMENTS", "2000"))
# 单次提交的原始十六进制字符总数上限(空白/前缀不计)
MAX_HEX_CHARS = MAX_TOTAL_BYTES * 2

app = FastAPI(title="单向抓包负载重组", version="1.0.0")


class FragmentIn(BaseModel):
    offset: int = Field(..., description="分片起始字节偏移")
    hex: str = Field(..., description="分片十六进制内容")
    label: str | None = Field(None, description="可选分片名称/序号")


class ReassembleIn(BaseModel):
    expected_length: int = Field(..., ge=0, description="预期还原的总字节数")
    fragments: list[FragmentIn] = Field(default_factory=list)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/limits")
def limits() -> dict:
    return {
        "max_expected": MAX_EXPECTED,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "max_fragments": MAX_FRAGMENTS,
    }


def _build(payload: ReassembleIn) -> list[Fragment]:
    out: list[Fragment] = []
    for idx, item in enumerate(payload.fragments):
        if item.offset < 0:
            raise HTTPException(
                status_code=422, detail=f"第 {idx + 1} 个分片的起始偏移不能为负"
            )
        data = parse_hex(item.hex, max_chars=MAX_HEX_CHARS, index=idx)
        out.append(
            Fragment(offset=item.offset, data=data, label=(item.label or "").strip())
        )
    return out


def _run(payload: ReassembleIn) -> dict:
    try:
        fragments = _build(payload)
        return reassemble(
            payload.expected_length,
            fragments,
            max_expected=MAX_EXPECTED,
            max_total_bytes=MAX_TOTAL_BYTES,
            max_fragments=MAX_FRAGMENTS,
            max_hex_chars=MAX_HEX_CHARS,
        )
    except ReassemblyLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except ReassemblyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/reassemble")
def api_reassemble(payload: ReassembleIn) -> dict:
    return _run(payload)


@app.post("/api/download")
def api_download(payload: ReassembleIn) -> Response:
    """下载前服务端重新校验:必须完整且无冲突才返回还原的二进制。"""
    result = _run(payload)
    if not result["can_download"] or result["payload_hex"] is None:
        reasons = []
        if result["stats"]["gap_bytes"]:
            reasons.append(f"存在 {result['stats']['gap_bytes']} 字节缺口")
        if result["stats"]["conflict_bytes"]:
            reasons.append(f"存在 {result['stats']['conflict_bytes']} 字节冲突")
        if result["expected_length"] == 0:
            reasons.append("预期长度为 0")
        raise HTTPException(
            status_code=409,
            detail="范围不完整或存在冲突,无法下载:" + ";".join(reasons),
        )
    data = bytes.fromhex(result["payload_hex"])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="reassembled.bin"',
            "Content-Length": str(len(data)),
        },
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
