from __future__ import annotations

import time
from app.services.qa.agents import (
    EvidenceAgent,
    IndexPerFileAgent,
    MixerAgent,
    NormalizeChunksAgent,
    PlannerAgent,
    QGenAgent,
    SetupAgent,
    VerifierAgent,
)
from typing import Callable

from app.services.qa.types import FileInput, QAContext
from app.services.qa.utils import merge_per_file_outputs


def generate_questions_for_files(
    files: list[FileInput],
    requested_total: int,
    difficulty: str,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[list[dict], dict]:
    started = time.perf_counter()
    ctx = QAContext(
        files=files,
        requested_total=requested_total,
        difficulty=difficulty,
        should_cancel=should_cancel,
    )

    pipeline = [
        SetupAgent(),
        NormalizeChunksAgent(),
        IndexPerFileAgent(),
        PlannerAgent(),
        EvidenceAgent(),
        QGenAgent(),
        VerifierAgent(),
        MixerAgent(),
    ]
    for agent in pipeline:
        agent_started = time.perf_counter()
        ctx = agent.run(ctx)
        stages = ctx.metrics.setdefault("pipeline_stages_sec", {})
        stages[agent.__class__.__name__] = time.perf_counter() - agent_started

    result = ctx.metrics.pop("_result", [])
    total_sec = time.perf_counter() - started
    ctx.metrics["pipeline_total_sec"] = total_sec
    final_count = int(ctx.metrics.get("final_count", len(result)))
    ctx.metrics["requested_total"] = requested_total
    ctx.metrics["final_count"] = final_count
    ctx.metrics["generation_throughput_qps"] = final_count / max(total_sec, 1e-9)
    return result, ctx.metrics


__all__ = ["generate_questions_for_files", "merge_per_file_outputs"]
