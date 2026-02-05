from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import FileRecord, GenerationJob, Topic, User
from app.services.chunking import chunk_text
from app.services.dedupe import dedupe_questions
from app.services.extraction import extract_text
from app.services.exporter import export_apkg
from app.services.qa import FileInput, generate_questions_for_files, merge_per_file_outputs
from app.services.storage import export_storage_dir, read_encrypted_file

logger = logging.getLogger(__name__)

_STAGE_PROGRESS = {
    "extracting": 15,
    "chunking": 30,
    "generating": 65,
    "deduping": 80,
    "exporting": 90,
    "done": 100,
}


@dataclass
class FilePayload:
    filename: str
    text: str


async def _update_job(session: AsyncSession, job: GenerationJob, **kwargs) -> None:
    for key, value in kwargs.items():
        setattr(job, key, value)
    await session.commit()


async def _load_job(session: AsyncSession, job_id: str) -> GenerationJob:
    job_uuid = uuid.UUID(job_id)
    result = await session.execute(select(GenerationJob).where(GenerationJob.id == job_uuid))
    job = result.scalar_one_or_none()
    if not job:
        raise ValueError("Job not found")
    return job


async def _is_cancelled(session: AsyncSession, job: GenerationJob) -> bool:
    await session.refresh(job)
    return job.status == "cancelled"


async def _load_topic(session: AsyncSession, topic_id: uuid.UUID) -> Topic:
    result = await session.execute(select(Topic).where(Topic.id == topic_id))
    return result.scalar_one()


async def _load_user(session: AsyncSession, user_id: int) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one()


async def _load_files(session: AsyncSession, topic_id: uuid.UUID) -> list[FileRecord]:
    result = await session.execute(
        select(FileRecord).where(FileRecord.topic_id == topic_id, FileRecord.deleted_at.is_(None))
    )
    return result.scalars().all()


def _stage_progress(stage: str) -> int:
    return _STAGE_PROGRESS.get(stage, 0)


def _build_file_inputs(topic_id: uuid.UUID, payloads: Iterable[FilePayload]) -> list[FileInput]:
    inputs: list[FileInput] = []
    for payload in payloads:
        chunks = [
            {"text": chunk, "source": payload.filename, "index": idx}
            for idx, chunk in enumerate(chunk_text(payload.text))
        ]
        if not chunks:
            continue
        inputs.append(
            FileInput(
                file_id=f"{topic_id}:{payload.filename}",
                file_name=payload.filename,
                chunks=chunks,
            )
        )
    return inputs


async def run_generation_job(job_id: str) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            logger.info("Start job %s", job_id)
            job = await _load_job(session, job_id)
            if await _is_cancelled(session, job):
                logger.info("Job %s cancelled before start", job_id)
                return
            try:
                topic = await _load_topic(session, job.topic_id)
                user = await _load_user(session, job.user_id)
                files = await _load_files(session, job.topic_id)
                if not files:
                    await _update_job(
                        session,
                        job,
                        status="failed",
                        stage="done",
                        progress=100,
                        error_message="No files available for generation",
                        finished_at=datetime.utcnow(),
                    )
                    return

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled before generation", job_id)
                    return

                await _update_job(
                    session,
                    job,
                    status="running",
                    stage="extracting",
                    progress=_stage_progress("extracting"),
                )

                payloads: list[FilePayload] = []
                for record in files:
                    content = read_encrypted_file(record.storage_path, record.encryption_nonce)
                    text = extract_text(record.mime_type, content)
                    payloads.append(FilePayload(filename=record.original_filename, text=text))

                await _update_job(session, job, stage="chunking", progress=_stage_progress("chunking"))

                params = job.params_json
                requested_total = int(params.get("number_of_questions", 20))
                difficulty = params.get("difficulty", "medium")
                mode = params.get("mode", "merged")
                include_answers = bool(params.get("include_answers", True))

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled before LLM call", job_id)
                    return

                file_inputs = _build_file_inputs(topic.id, payloads)
                if not file_inputs:
                    await _update_job(
                        session,
                        job,
                        status="failed",
                        stage="done",
                        progress=100,
                        error_message="No extractable text",
                        finished_at=datetime.utcnow(),
                    )
                    return

                await _update_job(session, job, stage="generating", progress=_stage_progress("generating"))

                questions: list[dict]
                if mode == "per_file":
                    outputs: list[list[dict]] = []
                    per_file_count = max(3, requested_total // max(1, len(file_inputs)))
                    for file_input in file_inputs:
                        per_questions, metrics = generate_questions_for_files(
                            [file_input],
                            per_file_count,
                            difficulty,
                        )
                        logger.info("Agent metrics (%s): %s", file_input.file_name, metrics)
                        outputs.append(per_questions)
                    questions = merge_per_file_outputs(outputs, requested_total)
                else:
                    questions, metrics = generate_questions_for_files(file_inputs, requested_total, difficulty)
                    logger.info("Agent metrics: %s", metrics)

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled after LLM call", job_id)
                    return

                await _update_job(session, job, stage="deduping", progress=_stage_progress("deduping"))
                deduped = dedupe_questions(questions)
                if len(deduped) < requested_total:
                    deduped.extend(questions)
                questions = deduped[:requested_total]
                if not include_answers:
                    for item in questions:
                        item.pop("answer", None)
                        if item.get("type") == "mcq":
                            item.pop("correct_index", None)

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled before export", job_id)
                    return

                await _update_job(session, job, stage="exporting", progress=_stage_progress("exporting"))

                export_dir = export_storage_dir(str(topic.id))
                apkg_path = export_dir / f"questions_{job.id}.apkg"
                export_apkg(apkg_path, topic.title, questions)

                job.result_paths = {"apkg": str(apkg_path)}
                job.status = "done"
                job.stage = "done"
                job.progress = 100
                job.finished_at = datetime.utcnow()
                await session.commit()
                logger.info("Finished job %s", job_id)

                if settings.job_webhook_url:
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            settings.job_webhook_url,
                            json={
                                "job_id": str(job.id),
                                "topic_id": str(topic.id),
                                "user_id": job.user_id,
                                "telegram_id": user.telegram_id,
                            },
                        )
            except Exception as exc:
                await _update_job(
                    session,
                    job,
                    status="failed",
                    stage="done",
                    progress=100,
                    error_message=str(exc),
                    finished_at=datetime.utcnow(),
                )
    finally:
        await engine.dispose()
