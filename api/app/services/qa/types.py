from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings


@dataclass
class FileInput:
    file_id: str
    file_name: str
    chunks: list[dict[str, Any]]


@dataclass
class QAContext:
    files: list[FileInput]
    requested_total: int
    difficulty: str
    llm: Any | None = None
    embeddings: Embeddings | None = None
    stores: dict[str, Chroma] = field(default_factory=dict)
    normalized_chunks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    per_file_questions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    per_file_topics: dict[str, list[str]] = field(default_factory=dict)
    per_file_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


class Agent:
    def run(self, ctx: QAContext) -> QAContext:
        raise NotImplementedError
