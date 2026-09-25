"""FastAPI 入口：重组 API、结果下载与静态页面托管。"""

from __future__ import annotations

import os
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .reassembly import ReassemblyError, assemble

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# ---- 可调配置（环境变量） -------------------------------------------------
MAX_EXPECTED_LENGTH = int(os.getenv("MAX_EXPECTED_LENGTH", str(16 * 1024 * 1024)))
MAX_FRAGMENTS = int(os.getenv("MAX_FRAGMENTS", "4096"))
MAX_TOTAL_INPUT = int(os.getenv("MAX_TOTAL_INPUT", str(64 * 1024 * 1024)))
# 重组结果（含可下载字节）最多保留多少份 / 多久（秒）
JOB_RETENTION_COUNT = int(os.getenv("JOB_RETENTION_COUNT", "100"))
JOB_RETENTION_SECONDS = int(os.getenv("JOB_RETENTION_SECONDS", "1800"))


class FragmentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    offset: StrictInt = Field(..., description="分片起始字节偏移，0 起算")
    data: str = Field(..., description="分片原始内容的十六进制表示")


class ReassembleIn(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    expected_length: StrictInt = Field(..., ge=0, description="预期还原负载的总字节数")
    fragments: list[FragmentIn] = Field(..., min_length=1)


# job_id -> (created_at, payload_bytes)
_jobs: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()


def _store_job(payload: bytes) -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = (time.time(), payload)
    now = time.time()
    # 先按时间淘汰过期结果
    expired = [
        jid for jid, (created_at, _) in _jobs.items()
        if now - created_at > JOB_RETENTION_SECONDS
    ]
    for jid in expired:
        _jobs.pop(jid, None)
    # 再按容量淘汰最旧结果
    while len(_jobs) > JOB_RETENTION_COUNT:
        _jobs.popitem(last=False)
    return job_id


app = FastAPI(
    title="单向抓包负载重组工具",
    description="按字节偏移重组乱序分片，标注重叠冲突与覆盖缺口。",
    version="1.0.0",
)


@app.exception_handler(ReassemblyError)
async def reassembly_error_handler(_request, exc: ReassemblyError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "limits": {
            "max_expected_length": MAX_EXPECTED_LENGTH,
            "max_fragments": MAX_FRAGMENTS,
            "max_total_input": MAX_TOTAL_INPUT,
        },
    }


@app.post("/api/reassemble")
async def reassemble(body: ReassembleIn) -> dict[str, Any]:
    if body.expected_length > MAX_EXPECTED_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"预期字节数 {body.expected_length} 超过上限 {MAX_EXPECTED_LENGTH}",
        )
    if len(body.fragments) > MAX_FRAGMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"分片数量 {len(body.fragments)} 超过上限 {MAX_FRAGMENTS}",
        )

    report, payload = assemble(
        body.expected_length,
        [f.model_dump() for f in body.fragments],
        max_fragments=MAX_FRAGMENTS,
        max_total_input=MAX_TOTAL_INPUT,
    )

    if payload is not None:
        report["job_id"] = _store_job(payload)
        report["download_url"] = f"/api/download/{report['job_id']}"
    else:
        report["job_id"] = None
        report["download_url"] = None
    return report


@app.get("/api/download/{job_id}")
async def download(job_id: str) -> Response:
    entry = _jobs.get(job_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="结果不存在或已过期，请重新重组")
    _, payload = entry
    _jobs.move_to_end(job_id)
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="reassembled_{job_id[:8]}.bin"',
            "Content-Length": str(len(payload)),
        },
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
