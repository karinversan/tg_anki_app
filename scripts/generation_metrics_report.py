#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = os.path.dirname(os.path.dirname(__file__))
API_ROOT = os.path.join(ROOT, "api")
if API_ROOT not in sys.path:
    sys.path.append(API_ROOT)

from app.core.config import settings  # noqa: E402
from app.db.models import GenerationJob  # noqa: E402


def to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(values: Iterable[float]) -> dict[str, float]:
    data = [v for v in values if isinstance(v, (int, float))]
    if not data:
        return {}
    return {
        "count": float(len(data)),
        "min": min(data),
        "mean": mean(data),
        "p50": percentile(data, 0.50),
        "p95": percentile(data, 0.95),
        "max": max(data),
    }


def fmt(stat: dict[str, float], unit: str) -> str:
    if not stat:
        return "n/a"
    return (
        f"mean={stat['mean']:.3f}{unit}, "
        f"p50={stat['p50']:.3f}{unit}, "
        f"p95={stat['p95']:.3f}{unit}, "
        f"min={stat['min']:.3f}{unit}, "
        f"max={stat['max']:.3f}{unit}"
    )


async def fetch_jobs(limit: int) -> list[GenerationJob]:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            stmt = (
                select(GenerationJob)
                .where(GenerationJob.status == "done", GenerationJob.metrics_json.is_not(None))
                .order_by(desc(GenerationJob.created_at))
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
    finally:
        await engine.dispose()


def build_report(jobs: list[GenerationJob]) -> tuple[dict[str, Any], str]:
    providers = Counter()
    models = Counter()
    e2e_seconds: list[float] = []
    generation_qps: list[float] = []
    e2e_qps: list[float] = []
    llm_latency_ms: list[float] = []
    llm_calls: list[float] = []
    dedupe_removed: list[float] = []
    coverage_ratio: list[float] = []
    extracting_sec: list[float] = []
    chunking_sec: list[float] = []
    generating_sec: list[float] = []
    deduping_sec: list[float] = []
    exporting_sec: list[float] = []

    for job in jobs:
        data = job.metrics_json or {}
        if not isinstance(data, dict):
            continue
        provider = data.get("llm_provider")
        if isinstance(provider, str) and provider:
            providers[provider] += 1
        model = data.get("llm_model")
        if isinstance(model, str) and model:
            models[model] += 1

        value = to_float(data.get("total_elapsed_sec"))
        if value is not None:
            e2e_seconds.append(value)
        value = to_float(data.get("throughput_qps_end_to_end"))
        if value is not None:
            e2e_qps.append(value)
        value = to_float(data.get("throughput_qps_generation"))
        if value is not None:
            generation_qps.append(value)
        value = to_float(data.get("dedupe_removed"))
        if value is not None:
            dedupe_removed.append(value)
        value = to_float(data.get("coverage_ratio"))
        if value is not None:
            coverage_ratio.append(value)

        stage_seconds = data.get("stage_seconds")
        if isinstance(stage_seconds, dict):
            value = to_float(stage_seconds.get("extracting"))
            if value is not None:
                extracting_sec.append(value)
            value = to_float(stage_seconds.get("chunking"))
            if value is not None:
                chunking_sec.append(value)
            value = to_float(stage_seconds.get("generating"))
            if value is not None:
                generating_sec.append(value)
            value = to_float(stage_seconds.get("deduping"))
            if value is not None:
                deduping_sec.append(value)
            value = to_float(stage_seconds.get("exporting"))
            if value is not None:
                exporting_sec.append(value)

        llm = None
        agent_metrics = data.get("agent_metrics")
        if isinstance(agent_metrics, dict):
            llm_val = agent_metrics.get("llm")
            if isinstance(llm_val, dict):
                llm = llm_val
        if llm:
            value = to_float(llm.get("latency_avg_sec"))
            if value is not None:
                llm_latency_ms.append(value * 1000.0)
            value = to_float(llm.get("calls_total"))
            if value is not None:
                llm_calls.append(value)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "jobs_analyzed": len(jobs),
        "providers": dict(providers),
        "models": dict(models),
        "e2e_seconds": summarize(e2e_seconds),
        "e2e_qps": summarize(e2e_qps),
        "generation_qps": summarize(generation_qps),
        "llm_latency_ms": summarize(llm_latency_ms),
        "llm_calls": summarize(llm_calls),
        "dedupe_removed": summarize(dedupe_removed),
        "coverage_ratio": summarize(coverage_ratio),
        "stage_extracting_sec": summarize(extracting_sec),
        "stage_chunking_sec": summarize(chunking_sec),
        "stage_generating_sec": summarize(generating_sec),
        "stage_deduping_sec": summarize(deduping_sec),
        "stage_exporting_sec": summarize(exporting_sec),
    }

    markdown = "\n".join(
        [
            "# Generation Metrics Report",
            f"- Generated (UTC): {summary['generated_at']}",
            f"- Jobs analyzed: {summary['jobs_analyzed']}",
            f"- Providers: {json.dumps(summary['providers'], ensure_ascii=False)}",
            f"- Models: {json.dumps(summary['models'], ensure_ascii=False)}",
            "",
            "## Throughput",
            f"- End-to-end time: {fmt(summary['e2e_seconds'], 's')}",
            f"- End-to-end speed: {fmt(summary['e2e_qps'], ' q/s')}",
            f"- Generation speed: {fmt(summary['generation_qps'], ' q/s')}",
            "",
            "## LLM",
            f"- Calls per job: {fmt(summary['llm_calls'], '')}",
            f"- Avg LLM latency: {fmt(summary['llm_latency_ms'], 'ms')}",
            "",
            "## Quality",
            f"- Dedupe removed: {fmt(summary['dedupe_removed'], '')}",
            f"- Coverage ratio: {fmt(summary['coverage_ratio'], '')}",
            "",
            "## Stage Times",
            f"- Extracting: {fmt(summary['stage_extracting_sec'], 's')}",
            f"- Chunking: {fmt(summary['stage_chunking_sec'], 's')}",
            f"- Generating: {fmt(summary['stage_generating_sec'], 's')}",
            f"- Deduping: {fmt(summary['stage_deduping_sec'], 's')}",
            f"- Exporting: {fmt(summary['stage_exporting_sec'], 's')}",
        ]
    )
    return summary, markdown


async def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize generation metrics from completed jobs.")
    parser.add_argument("--limit", type=int, default=50, help="How many latest jobs to analyze.")
    parser.add_argument("--json-out", type=str, default="", help="Optional path to write JSON summary.")
    parser.add_argument("--md-out", type=str, default="", help="Optional path to write markdown summary.")
    args = parser.parse_args()

    try:
        jobs = await fetch_jobs(limit=max(1, args.limit))
    except Exception as exc:
        raise SystemExit(
            f"Failed to load jobs from database ({settings.database_url}). "
            "Ensure DB is running and DATABASE_URL is correct."
        ) from exc
    summary, markdown = build_report(jobs)
    print(markdown)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as handle:
            handle.write(markdown + "\n")


if __name__ == "__main__":
    asyncio.run(main())
