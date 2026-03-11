from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.core.config import settings
from app.db.models import User
from app.db.session import get_session
from app.services.metrics_report import build_report, fetch_jobs

router = APIRouter(prefix="/admin", tags=["admin"])


class MetricsReportRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=1000)


class MetricsReportResponse(BaseModel):
    report_id: str
    generated_at: str
    jobs_analyzed: int
    summary: dict[str, Any]
    download_json_url: str
    download_md_url: str


def _reports_dir() -> Path:
    path = Path(settings.storage_path) / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/metrics/report", response_model=MetricsReportResponse)
async def generate_metrics_report(
    payload: MetricsReportRequest,
    _: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> MetricsReportResponse:
    jobs = await fetch_jobs(session, payload.limit)
    summary, markdown = build_report(jobs)

    report_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    base_path = _reports_dir() / report_id
    json_path = base_path.with_suffix(".json")
    md_path = base_path.with_suffix(".md")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown + "\n", encoding="utf-8")

    return MetricsReportResponse(
        report_id=report_id,
        generated_at=str(summary.get("generated_at", "")),
        jobs_analyzed=int(summary.get("jobs_analyzed", 0)),
        summary=summary,
        download_json_url=f"/admin/metrics/report/{report_id}/download/json",
        download_md_url=f"/admin/metrics/report/{report_id}/download/md",
    )


@router.get("/metrics/report/{report_id}/download/{format}")
async def download_metrics_report(
    report_id: str,
    format: str,
    _: User = Depends(get_current_admin),
) -> FileResponse:
    normalized = format.strip().lower()
    if normalized not in {"json", "md"}:
        raise HTTPException(status_code=400, detail="Unsupported format")
    file_path = (_reports_dir() / report_id).with_suffix(f".{normalized}")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    media_type = "application/json" if normalized == "json" else "text/markdown; charset=utf-8"
    return FileResponse(str(file_path), media_type=media_type, filename=file_path.name)
