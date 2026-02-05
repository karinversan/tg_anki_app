from __future__ import annotations

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
from app.services.qa.types import FileInput, QAContext
from app.services.qa.utils import merge_per_file_outputs


def generate_questions_for_files(
    files: list[FileInput],
    requested_total: int,
    difficulty: str,
) -> tuple[list[dict], dict]:
    ctx = QAContext(files=files, requested_total=requested_total, difficulty=difficulty)

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
        ctx = agent.run(ctx)

    result = ctx.metrics.pop("_result", [])
    return result, ctx.metrics


__all__ = ["generate_questions_for_files", "merge_per_file_outputs"]
