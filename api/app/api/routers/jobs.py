from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_job_for_user, get_topic_for_user
from app.core.config import settings
from app.db.models import GenerationJob, Topic, User
from app.db.session import get_session
from app.schemas.job import JobCreate, JobOut
from app.services.celery_app import celery_app
import logging
from app.services.cache import get_redis
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/topics/{topic_id}/jobs", tags=["jobs"])
logger = logging.getLogger("api.jobs")


@router.post("/", response_model=JobOut)
async def create_job(
    topic_id: UUID,
    payload: JobCreate,
    _topic: Topic = Depends(get_topic_for_user),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    redis = get_redis()
    try:
        await check_rate_limit(
            redis,
            f"rate:gen:{user.id}",
            settings.rate_limit_generations_per_minute,
            60,
        )
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    params = payload.model_dump()
    job = GenerationJob(
        topic_id=topic_id,
        user_id=user.id,
        mode=payload.mode,
        params_json=params,
        status="queued",
        progress=0,
        stage="queued",
        created_at=datetime.utcnow(),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    job_id = str(job.id)
    celery_app.send_task("worker.tasks.generate_questions", args=[job_id], task_id=job_id)
    logger.info("Enqueued job %s for topic %s", job_id, topic_id)
    return JobOut.model_validate(job)


@router.get("/latest", response_model=JobOut | None)
async def latest_job(
    topic_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut | None:
    stmt = (
        select(GenerationJob)
        .where(GenerationJob.topic_id == topic_id, GenerationJob.user_id == user.id)
        .order_by(GenerationJob.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        return None
    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
async def job_status(
    topic_id: UUID,
    job_id: UUID,
    job: GenerationJob = Depends(get_job_for_user),
) -> JobOut:
    return JobOut.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    topic_id: UUID,
    job_id: UUID,
    job: GenerationJob = Depends(get_job_for_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    if job.status in {"done", "failed", "cancelled"}:
        return JobOut.model_validate(job)
    job.status = "cancelled"
    job.stage = "done"
    job.progress = 100
    job.error_message = "Cancelled by user"
    job.finished_at = datetime.utcnow()
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    topic_id: UUID,
    job_id: UUID,
    job: GenerationJob = Depends(get_job_for_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    new_job = GenerationJob(
        topic_id=topic_id,
        user_id=job.user_id,
        mode=job.mode,
        params_json=job.params_json,
        status="queued",
        progress=0,
        stage="queued",
        created_at=datetime.utcnow(),
    )
    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)

    new_job_id = str(new_job.id)
    celery_app.send_task("worker.tasks.generate_questions", args=[new_job_id], task_id=new_job_id)
    logger.info("Retried job %s from %s", new_job_id, job_id)
    return JobOut.model_validate(new_job)


@router.get("/{job_id}/download/{format}")
async def download_result(
    topic_id: UUID,
    job_id: UUID,
    format: str,
    job: GenerationJob = Depends(get_job_for_user),
) -> FileResponse:
    if not job.result_paths:
        raise HTTPException(status_code=404, detail="Result not found")
    path = job.result_paths.get(format)
    if not path:
        raise HTTPException(status_code=404, detail="Format not available")
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    media = "application/octet-stream"
    if format == "json":
        media = "application/json"
    if format == "docx":
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if format == "apkg":
        media = "application/octet-stream"
    return FileResponse(str(file_path), media_type=media, filename=file_path.name)


@router.post("/{job_id}/send")
async def send_result(
    topic_id: UUID,
    job_id: UUID,
    user: User = Depends(get_current_user),
    job: GenerationJob = Depends(get_job_for_user),
) -> dict:
    if not job.result_paths:
        raise HTTPException(status_code=409, detail="APKG not available for this job")
    path = job.result_paths.get("apkg")
    if not path:
        raise HTTPException(status_code=409, detail="APKG not available for this job")
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")

    telegram_url = f"https://api.telegram.org/bot{settings.bot_token}/sendDocument"
    caption = f"Anki package for topic: {topic_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        with file_path.open("rb") as handle:
            files = {"document": (file_path.name, handle, "application/octet-stream")}
            data = {"chat_id": str(user.telegram_id), "caption": caption}
            response = await client.post(telegram_url, data=data, files=files)
            response.raise_for_status()

    return {"ok": True}
