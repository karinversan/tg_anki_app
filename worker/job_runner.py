from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

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


@dataclass
class FileExtractionInput:
    filename: str
    mime_type: str
    storage_path: str
    encryption_nonce: str


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


def _extract_payload(data: FileExtractionInput) -> FilePayload:
    content = read_encrypted_file(data.storage_path, data.encryption_nonce)
    text = extract_text(data.mime_type, content)
    return FilePayload(filename=data.filename, text=text)


async def _extract_payloads(
    files: list[FileRecord],
    should_cancel: Callable[[], bool],
) -> list[FilePayload]:
    concurrency = max(1, settings.job_extract_concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    inputs = [
        FileExtractionInput(
            filename=record.original_filename,
            mime_type=record.mime_type,
            storage_path=record.storage_path,
            encryption_nonce=record.encryption_nonce,
        )
        for record in files
    ]

    async def run_one(data: FileExtractionInput) -> FilePayload | None:
        async with semaphore:
            if should_cancel():
                return None
            return await asyncio.to_thread(_extract_payload, data)

    results = await asyncio.gather(*(run_one(data) for data in inputs))
    return [item for item in results if item is not None]


def _build_file_input(topic_id: uuid.UUID, payload: FilePayload) -> tuple[FileInput | None, int, int]:
    chunks = [
        {"text": chunk, "source": payload.filename, "index": idx}
        for idx, chunk in enumerate(chunk_text(payload.text))
    ]
    if not chunks:
        return None, 0, 0
    chunk_chars = sum(len(str(chunk.get("text", ""))) for chunk in chunks)
    file_input = FileInput(
        file_id=f"{topic_id}:{payload.filename}",
        file_name=payload.filename,
        chunks=chunks,
    )
    return file_input, len(chunks), chunk_chars


async def _build_file_inputs(
    topic_id: uuid.UUID,
    payloads: list[FilePayload],
    should_cancel: Callable[[], bool],
) -> tuple[list[FileInput], dict[str, float | int]]:
    concurrency = max(1, settings.job_chunk_concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(payload: FilePayload) -> tuple[FileInput | None, int, int]:
        async with semaphore:
            if should_cancel():
                return None, 0, 0
            return await asyncio.to_thread(_build_file_input, topic_id, payload)

    results = await asyncio.gather(*(run_one(payload) for payload in payloads))
    inputs: list[FileInput] = []
    chunk_count = 0
    chunk_chars_total = 0
    for file_input, chunks_len, chunk_chars in results:
        if file_input is None:
            continue
        inputs.append(file_input)
        chunk_count += chunks_len
        chunk_chars_total += chunk_chars
    avg_chunk_chars = chunk_chars_total / max(1, chunk_count)
    return inputs, {
        "chunk_count": chunk_count,
        "chunk_chars_total": chunk_chars_total,
        "avg_chunk_chars": avg_chunk_chars,
    }


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _round_dict_values(data: dict[str, Any], digits: int = 4) -> dict[str, Any]:
    rounded: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, float):
            rounded[key] = round(value, digits)
        elif isinstance(value, dict):
            rounded[key] = _round_dict_values(value, digits=digits)
        else:
            rounded[key] = value
    return rounded


def _estimate_generation_seconds(requested_total: int, file_count: int, chunk_count: int) -> float:
    base = 15.0 + max(1, requested_total) * 2.5 + max(1, file_count) * 4.0
    chunk_component = min(240.0, max(0, chunk_count) * 0.08)
    return min(1800.0, base + chunk_component)


def _build_quality_metrics(
    questions: list[dict[str, Any]],
    *,
    requested_total: int,
    input_files: int,
) -> dict[str, Any]:
    final_questions = len(questions)
    if final_questions <= 0:
        return {
            "quality_score": 0.0,
            "source_coverage_ratio": 0.0,
            "unique_source_count": 0,
            "question_uniqueness_ratio": 0.0,
            "question_type_distribution": {},
        }

    source_backed = 0
    all_sources: set[str] = set()
    type_distribution: dict[str, int] = {}
    seen_questions: set[str] = set()
    for item in questions:
        q_type = str(item.get("type", "open")).strip().lower() or "open"
        type_distribution[q_type] = type_distribution.get(q_type, 0) + 1

        question_text = str(item.get("question", "")).strip().lower()
        if question_text:
            seen_questions.add(question_text)

        sources = item.get("sources")
        source_list: list[str] = []
        if isinstance(sources, str):
            source_list = [s.strip() for s in sources.split(",") if s.strip()]
        elif isinstance(sources, list):
            source_list = [str(s).strip() for s in sources if str(s).strip()]
        if source_list:
            source_backed += 1
            all_sources.update(source_list)

    source_coverage_ratio = source_backed / max(1, final_questions)
    completion_ratio = final_questions / max(1, requested_total)
    source_diversity_ratio = min(1.0, len(all_sources) / max(1, input_files))
    question_uniqueness_ratio = len(seen_questions) / max(1, final_questions)
    quality_score = 100.0 * (
        0.4 * min(1.0, completion_ratio)
        + 0.35 * source_coverage_ratio
        + 0.15 * source_diversity_ratio
        + 0.10 * question_uniqueness_ratio
    )

    return {
        "quality_score": round(max(0.0, min(100.0, quality_score)), 1),
        "source_coverage_ratio": round(source_coverage_ratio, 4),
        "unique_source_count": len(all_sources),
        "question_uniqueness_ratio": round(question_uniqueness_ratio, 4),
        "question_type_distribution": type_distribution,
    }


async def _update_generating_runtime(
    session: AsyncSession,
    job: GenerationJob,
    *,
    generation_started: float,
    progress: int,
    units_done: int,
    units_total: int,
    eta_seconds: float | None,
    mode: str,
    current_file: str | None = None,
) -> None:
    runtime: dict[str, Any] = {
        "stage": "generating",
        "mode": mode,
        "stage_elapsed_sec": round(max(0.0, time.perf_counter() - generation_started), 1),
        "units_done": max(0, units_done),
        "units_total": max(1, units_total),
        "eta_seconds": round(max(0.0, eta_seconds), 1) if eta_seconds is not None else None,
    }
    if current_file:
        runtime["current_file"] = current_file
    await _update_job(
        session,
        job,
        stage="generating",
        progress=max(_stage_progress("generating"), min(79, progress)),
        metrics_json={"runtime": runtime},
    )


def _aggregate_per_file_metrics(metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics_list:
        return {}
    aggregate: dict[str, Any] = {
        "pipeline_total_sec": 0.0,
        "final_count": 0,
        "raw_questions_total": 0,
    }
    llm_aggregate: dict[str, Any] = {
        "provider": None,
        "model": None,
        "platform": None,
        "machine": None,
        "acceleration": None,
        "calls_total": 0,
        "calls_failed": 0,
        "retries_total": 0,
        "latency_total_sec": 0.0,
        "latency_max_sec": 0.0,
        "prompt_chars_total": 0,
        "response_chars_total": 0,
        "prompt_tokens_total": 0.0,
        "output_tokens_total": 0.0,
    }
    pipeline_stage_totals: dict[str, float] = {}
    operation_counts: dict[str, int] = {}
    decode_speed_sum = 0.0
    decode_speed_weight = 0

    for metrics in metrics_list:
        aggregate["pipeline_total_sec"] += _to_float(metrics.get("pipeline_total_sec")) or 0.0
        aggregate["final_count"] += int(metrics.get("final_count", 0))
        aggregate["raw_questions_total"] += int(metrics.get("raw_questions_total", 0))
        stages = metrics.get("pipeline_stages_sec")
        if isinstance(stages, dict):
            for stage, stage_value in stages.items():
                pipeline_stage_totals[stage] = pipeline_stage_totals.get(stage, 0.0) + (
                    _to_float(stage_value) or 0.0
                )

        llm = metrics.get("llm")
        if not isinstance(llm, dict):
            continue
        for key in ("provider", "model", "platform", "machine", "acceleration"):
            if llm_aggregate.get(key) is None and llm.get(key) is not None:
                llm_aggregate[key] = llm.get(key)

        llm_aggregate["calls_total"] += int(llm.get("calls_total", 0))
        llm_aggregate["calls_failed"] += int(llm.get("calls_failed", 0))
        llm_aggregate["retries_total"] += int(llm.get("retries_total", 0))
        llm_aggregate["latency_total_sec"] += _to_float(llm.get("latency_total_sec")) or 0.0
        llm_aggregate["latency_max_sec"] = max(
            llm_aggregate["latency_max_sec"],
            _to_float(llm.get("latency_max_sec")) or 0.0,
        )
        llm_aggregate["prompt_chars_total"] += int(llm.get("prompt_chars_total", 0))
        llm_aggregate["response_chars_total"] += int(llm.get("response_chars_total", 0))
        llm_aggregate["prompt_tokens_total"] += _to_float(llm.get("prompt_tokens_total")) or 0.0
        llm_aggregate["output_tokens_total"] += _to_float(llm.get("output_tokens_total")) or 0.0

        operations = llm.get("operation_counts")
        if isinstance(operations, dict):
            for name, count in operations.items():
                operation_counts[name] = operation_counts.get(name, 0) + int(count)

        decode_speed = _to_float(llm.get("decode_tokens_per_sec_avg"))
        if decode_speed is not None:
            weight = int(llm.get("calls_total", 0))
            decode_speed_sum += decode_speed * max(1, weight)
            decode_speed_weight += max(1, weight)

    if llm_aggregate["calls_total"] > 0:
        llm_aggregate["latency_avg_sec"] = llm_aggregate["latency_total_sec"] / llm_aggregate["calls_total"]
    if decode_speed_weight > 0:
        llm_aggregate["decode_tokens_per_sec_avg"] = decode_speed_sum / decode_speed_weight
    if operation_counts:
        llm_aggregate["operation_counts"] = operation_counts

    aggregate["pipeline_stages_sec"] = pipeline_stage_totals
    aggregate["llm"] = llm_aggregate
    return aggregate


async def run_generation_job(job_id: str) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    cancel_event = asyncio.Event()
    poll_task: asyncio.Task | None = None
    job_started = time.perf_counter()
    stage_seconds: dict[str, float] = {}
    try:

        def should_cancel() -> bool:
            return cancel_event.is_set()

        async def poll_cancel() -> None:
            while not cancel_event.is_set():
                try:
                    async with session_factory() as poll_session:
                        job_row = await _load_job(poll_session, job_id)
                        if job_row.status == "cancelled":
                            cancel_event.set()
                            return
                except Exception:
                    pass
                await asyncio.sleep(1)

        poll_task = asyncio.create_task(poll_cancel())
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
                        metrics_json={"total_elapsed_sec": time.perf_counter() - job_started},
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

                extraction_started = time.perf_counter()
                input_bytes_total = sum(record.size_bytes for record in files)
                payloads = await _extract_payloads(files, should_cancel)
                if should_cancel():
                    logger.info("Job %s cancelled during extraction", job_id)
                    return
                stage_seconds["extracting"] = time.perf_counter() - extraction_started
                input_text_chars_total = sum(len(payload.text) for payload in payloads)

                await _update_job(session, job, stage="chunking", progress=_stage_progress("chunking"))

                params = job.params_json
                requested_total = int(params.get("number_of_questions", 20))
                difficulty = params.get("difficulty", "medium")
                mode = params.get("mode", "merged")
                include_answers = bool(params.get("include_answers", True))

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled before LLM call", job_id)
                    return

                chunking_started = time.perf_counter()
                file_inputs, chunk_stats = await _build_file_inputs(topic.id, payloads, should_cancel)
                stage_seconds["chunking"] = time.perf_counter() - chunking_started
                if not file_inputs:
                    await _update_job(
                        session,
                        job,
                        status="failed",
                        stage="done",
                        progress=100,
                        error_message="No extractable text",
                        metrics_json={
                            "input_files": len(files),
                            "input_bytes_total": input_bytes_total,
                            "input_text_chars_total": input_text_chars_total,
                            "stage_seconds": _round_dict_values(stage_seconds),
                            "total_elapsed_sec": time.perf_counter() - job_started,
                        },
                        finished_at=datetime.utcnow(),
                    )
                    return

                await _update_job(session, job, stage="generating", progress=_stage_progress("generating"))

                generation_started = time.perf_counter()
                questions: list[dict]
                generation_metrics: dict[str, Any]
                if mode == "per_file":
                    outputs: list[list[dict]] = []
                    per_file_metrics: list[dict[str, Any]] = []
                    per_file_count = max(3, requested_total // max(1, len(file_inputs)))
                    total_files = max(1, len(file_inputs))
                    for idx, file_input in enumerate(file_inputs, start=1):
                        if should_cancel():
                            logger.info("Job %s cancelled during generation", job_id)
                            return
                        completed = idx - 1
                        elapsed = time.perf_counter() - generation_started
                        eta = (elapsed / completed) * (total_files - completed) if completed > 0 else None
                        progress = 65 + int(14 * (completed / total_files))
                        await _update_generating_runtime(
                            session,
                            job,
                            generation_started=generation_started,
                            progress=progress,
                            units_done=completed,
                            units_total=total_files,
                            eta_seconds=eta,
                            mode=mode,
                            current_file=file_input.file_name,
                        )
                        step_task = asyncio.create_task(
                            asyncio.to_thread(
                                generate_questions_for_files,
                                [file_input],
                                per_file_count,
                                difficulty,
                                should_cancel,
                            )
                        )
                        while not step_task.done():
                            if should_cancel():
                                logger.info("Job %s cancellation requested during generation step", job_id)
                            await asyncio.sleep(3)
                            completed = idx - 1
                            elapsed = time.perf_counter() - generation_started
                            eta = (elapsed / completed) * (total_files - completed) if completed > 0 else None
                            progress = 65 + int(14 * (completed / total_files))
                            await _update_generating_runtime(
                                session,
                                job,
                                generation_started=generation_started,
                                progress=progress,
                                units_done=completed,
                                units_total=total_files,
                                eta_seconds=eta,
                                mode=mode,
                                current_file=file_input.file_name,
                            )
                        per_questions, metrics = await step_task
                        logger.info("Agent metrics (%s): %s", file_input.file_name, metrics)
                        outputs.append(per_questions)
                        per_file_metrics.append(metrics)
                        elapsed = time.perf_counter() - generation_started
                        completed = idx
                        eta = (elapsed / completed) * (total_files - completed) if completed < total_files else 0.0
                        progress = 65 + int(14 * (completed / total_files))
                        await _update_generating_runtime(
                            session,
                            job,
                            generation_started=generation_started,
                            progress=progress,
                            units_done=completed,
                            units_total=total_files,
                            eta_seconds=eta,
                            mode=mode,
                        )
                    if should_cancel():
                        logger.info("Job %s cancelled during generation", job_id)
                        return
                    questions = merge_per_file_outputs(outputs, requested_total)
                    generation_metrics = _aggregate_per_file_metrics(per_file_metrics)
                    generation_metrics["mode"] = "per_file"
                    generation_metrics["per_file_jobs"] = len(per_file_metrics)
                else:
                    estimated_generation_sec = _estimate_generation_seconds(
                        requested_total=requested_total,
                        file_count=len(file_inputs),
                        chunk_count=int(chunk_stats.get("chunk_count", 0)),
                    )
                    merge_task = asyncio.create_task(
                        asyncio.to_thread(
                            generate_questions_for_files,
                            file_inputs,
                            requested_total,
                            difficulty,
                            should_cancel,
                        )
                    )
                    while not merge_task.done():
                        if should_cancel():
                            logger.info("Job %s cancellation requested during merged generation", job_id)
                        await asyncio.sleep(3)
                        elapsed = time.perf_counter() - generation_started
                        # Keep runtime progress below 100% until generation is actually complete.
                        ratio = min(0.95, elapsed / max(estimated_generation_sec, 1e-9))
                        progress = 65 + int(14 * ratio)
                        eta = max(0.0, estimated_generation_sec - elapsed)
                        units_total = max(1, requested_total)
                        units_done_estimate = int(requested_total * ratio)
                        units_done = min(max(0, units_total - 1), units_done_estimate)
                        await _update_generating_runtime(
                            session,
                            job,
                            generation_started=generation_started,
                            progress=progress,
                            units_done=units_done,
                            units_total=units_total,
                            eta_seconds=eta,
                            mode=mode,
                        )
                    questions, generation_metrics = await merge_task
                    logger.info("Agent metrics: %s", generation_metrics)
                    generation_metrics["mode"] = "merged"
                    await _update_generating_runtime(
                        session,
                        job,
                        generation_started=generation_started,
                        progress=79,
                        units_done=max(1, requested_total),
                        units_total=max(1, requested_total),
                        eta_seconds=0.0,
                        mode=mode,
                    )
                stage_seconds["generating"] = time.perf_counter() - generation_started

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled after LLM call", job_id)
                    return

                await _update_job(session, job, stage="deduping", progress=_stage_progress("deduping"))
                deduping_started = time.perf_counter()
                before_dedupe_count = len(questions)
                deduped = dedupe_questions(questions)
                deduped_count = len(deduped)
                if deduped_count < requested_total:
                    deduped.extend(questions)
                questions = deduped[:requested_total]
                if not include_answers:
                    for item in questions:
                        item.pop("answer", None)
                        if item.get("type") == "mcq":
                            item.pop("correct_index", None)
                stage_seconds["deduping"] = time.perf_counter() - deduping_started

                if await _is_cancelled(session, job):
                    logger.info("Job %s cancelled before export", job_id)
                    return

                await _update_job(session, job, stage="exporting", progress=_stage_progress("exporting"))

                export_started = time.perf_counter()
                export_dir = export_storage_dir(str(topic.id))
                apkg_path = export_dir / f"questions_{job.id}.apkg"
                export_apkg(apkg_path, topic.title, questions)
                stage_seconds["exporting"] = time.perf_counter() - export_started

                total_elapsed_sec = time.perf_counter() - job_started
                final_questions = len(questions)
                generation_elapsed = stage_seconds.get("generating", 0.0)
                quality_metrics = _build_quality_metrics(
                    questions,
                    requested_total=requested_total,
                    input_files=len(files),
                )
                llm_provider = (settings.llm_provider or "ollama").strip().lower()
                if llm_provider in {"ollama", "local"}:
                    llm_model = settings.local_llm_model
                elif llm_provider == "openrouter":
                    llm_model = settings.openrouter_model
                else:
                    llm_model = settings.gemini_model
                metrics_json = {
                    "llm_provider": llm_provider,
                    "llm_model": llm_model,
                    "mode": mode,
                    "requested_questions": requested_total,
                    "generated_questions_before_dedupe": before_dedupe_count,
                    "deduped_questions": deduped_count,
                    "final_questions": final_questions,
                    "dedupe_removed": max(0, before_dedupe_count - deduped_count),
                    "coverage_ratio": final_questions / max(1, requested_total),
                    "input_files": len(files),
                    "indexed_files": len(file_inputs),
                    "input_bytes_total": input_bytes_total,
                    "input_text_chars_total": input_text_chars_total,
                    "extract_concurrency": max(1, settings.job_extract_concurrency),
                    "chunk_concurrency": max(1, settings.job_chunk_concurrency),
                    "chunk_count": int(chunk_stats.get("chunk_count", 0)),
                    "avg_chunk_chars": float(chunk_stats.get("avg_chunk_chars", 0.0)),
                    "stage_seconds": _round_dict_values(stage_seconds),
                    "total_elapsed_sec": round(total_elapsed_sec, 4),
                    "throughput_qps_end_to_end": round(final_questions / max(total_elapsed_sec, 1e-9), 4),
                    "throughput_qps_generation": round(final_questions / max(generation_elapsed, 1e-9), 4),
                    "agent_metrics": _round_dict_values(generation_metrics),
                    **quality_metrics,
                }

                job.result_paths = {"apkg": str(apkg_path)}
                job.metrics_json = metrics_json
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
                failure_metrics = {
                    "stage_seconds": _round_dict_values(stage_seconds),
                    "total_elapsed_sec": round(time.perf_counter() - job_started, 4),
                }
                await _update_job(
                    session,
                    job,
                    status="failed",
                    stage="done",
                    progress=100,
                    error_message=str(exc),
                    metrics_json=failure_metrics,
                    finished_at=datetime.utcnow(),
                )
    finally:
        cancel_event.set()
        if poll_task:
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)
        await engine.dispose()
