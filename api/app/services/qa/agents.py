from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_core.documents import Document
from langchain_chroma import Chroma

from app.core.config import settings
from app.services.dedupe import dedupe_questions
from app.services.qa.clients import build_embeddings, build_llm, invoke, llm_descriptor
from app.services.qa.types import Agent, QAContext
from app.services.qa.utils import (
    build_context_packet,
    cheap_dedupe,
    chunk_hash,
    normalize_text,
    normalize_chunks,
    normalize_question_items,
    safe_json_loads,
    to_anki_qa,
)

logger = logging.getLogger(__name__)


def _cancelled(ctx: QAContext) -> bool:
    return bool(ctx.should_cancel and ctx.should_cancel())


def _chroma_client_settings() -> ChromaSettings:
    # Disable Chroma telemetry explicitly to avoid noisy posthog/capture runtime errors.
    return ChromaSettings(anonymized_telemetry=False)


def _per_file_quota(ctx: QAContext) -> int:
    files_count = max(1, len(ctx.files))
    return max(2, math.ceil(ctx.requested_total / files_count))


def _qgen_target_raw(quota: int) -> int:
    provider = (settings.llm_provider or "openrouter").strip().lower()
    # Remote providers are usually rate-limited, so keep less overgeneration.
    multiplier = 1.5 if provider in {"openrouter", "gemini"} else 2.0
    raw_target = max(quota + 4, int(round(quota * multiplier)))
    return max(6, raw_target)


def _planner_topics_target(quota: int) -> int:
    target_raw = _qgen_target_raw(quota)
    per_topic_cap = max(1, min(settings.rag_questions_per_topic, 6))
    needed = max(1, math.ceil(target_raw / per_topic_cap))
    min_topics = 1 if quota <= 4 else 2
    return max(min_topics, min(settings.rag_max_topics, needed))


def _parse_qgen_payload(
    llm: Any,
    raw: str,
    file_name: str,
    should_cancel: Any | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    max_retries = max(0, settings.llm_json_retries)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if should_cancel and should_cancel():
            return []
        try:
            data = safe_json_loads(raw)
            if isinstance(data, dict):
                data = data.get("items") or data.get("questions") or data.get("data")
            if isinstance(data, list):
                return data
            last_exc = ValueError(f"Unexpected JSON type: {type(data)}")
        except Exception as exc:
            last_exc = exc
        if attempt < max_retries:
            snippet = raw[: settings.llm_json_repair_max_chars]
            repair_prompt = (
                "Исправь ответ и верни ТОЛЬКО валидный JSON-объект вида {\"items\": [...]}.\n"
                "Никакого текста вне JSON. Если исправить нельзя, верни {\"items\": []}.\n\n"
                f"ОТВЕТ:\n{snippet}"
            )
            raw = invoke(
                llm,
                repair_prompt,
                should_cancel=should_cancel,
                metrics=metrics,
                operation="json_repair",
            )
    logger.warning("QGenAgent parse failed (%s): %s", file_name, last_exc)
    return []


def _parse_topic_list(
    llm: Any,
    raw: str,
    file_name: str,
    should_cancel: Any | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[str]:
    max_retries = max(0, settings.llm_json_retries)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if should_cancel and should_cancel():
            return []
        try:
            data = safe_json_loads(raw)
            if isinstance(data, dict):
                data = data.get("items") or data.get("topics") or data.get("data")
            if isinstance(data, list):
                topics = [str(x).strip() for x in data if str(x).strip()]
                if topics:
                    return topics
                last_exc = ValueError("Empty topics list")
            else:
                last_exc = ValueError(f"Unexpected JSON type: {type(data)}")
        except Exception as exc:
            last_exc = exc
        if attempt < max_retries:
            snippet = raw[: settings.llm_json_repair_max_chars]
            repair_prompt = (
                "Исправь ответ и верни ТОЛЬКО JSON-объект вида {\"items\": [...]}.\n"
                "Никакого текста вне JSON. Если исправить нельзя, верни {\"items\": []}.\n\n"
                f"ОТВЕТ:\n{snippet}"
            )
            raw = invoke(
                llm,
                repair_prompt,
                should_cancel=should_cancel,
                metrics=metrics,
                operation="json_repair",
            )
    logger.warning("PlannerAgent: topics parse failed (%s): %s", file_name, last_exc)
    return []


class SetupAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        ctx.llm = ctx.llm or build_llm()
        ctx.metrics.setdefault("llm", {}).update(llm_descriptor(ctx.llm))
        if settings.rag_use_embeddings:
            if ctx.embeddings is None:
                try:
                    ctx.embeddings = build_embeddings()
                except Exception as exc:
                    logger.warning("Embeddings init failed; falling back to lexical retrieval: %s", exc)
                    ctx.metrics["embeddings_error"] = str(exc)
                    ctx.metrics["embeddings_disabled"] = True
        else:
            ctx.metrics["embeddings_disabled"] = True
        ctx.metrics.setdefault("files", len(ctx.files))
        return ctx


class NormalizeChunksAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        normalized: dict[str, list[dict[str, Any]]] = {}
        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            chunks = normalize_chunks(f.chunks, f.file_name)
            if chunks:
                normalized[f.file_id] = chunks
        ctx.normalized_chunks = normalized
        ctx.metrics["normalized_files"] = len(normalized)
        return ctx


class IndexPerFileAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        if ctx.embeddings is None:
            ctx.metrics["indexed_files"] = 0
            return ctx
        base_dir = settings.chroma_path
        reuse_enabled = bool(settings.rag_reuse_vector_store)
        reused_files = 0
        indexed_new_files = 0
        persistent_client: chromadb.PersistentClient | None = None
        if reuse_enabled:
            try:
                persistent_client = chromadb.PersistentClient(
                    path=str(base_dir),
                    settings=_chroma_client_settings(),
                )
            except Exception as exc:
                logger.warning("Vector-store reuse init failed; fallback to rebuild: %s", exc)
                ctx.metrics["vector_reuse_error"] = str(exc)
                reuse_enabled = False

        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            if ctx.embeddings is None:
                break
            chunks = ctx.normalized_chunks.get(f.file_id, [])
            if not chunks:
                continue
            chunk_ids = [chunk_hash(f.file_id, ch) for ch in chunks]
            content_hash = hashlib.sha256("".join(chunk_ids).encode("utf-8")).hexdigest()
            collection_name = f"file_{re.sub(r'[^a-zA-Z0-9_-]+','_', f.file_id)[:32]}_{content_hash[:8]}"

            docs = [
                Document(
                    page_content=ch["text"],
                    metadata={
                        "file_id": f.file_id,
                        "source": ch.get("source", f.file_name),
                        "chunk_index": ch.get("index", 0),
                        "chunk_hash": chunk_hash(f.file_id, ch),
                    },
                )
                for ch in chunks
            ]

            if reuse_enabled and persistent_client is not None:
                try:
                    existing = persistent_client.get_collection(collection_name)
                    if existing.count() >= len(chunk_ids):
                        store = Chroma(
                            collection_name=collection_name,
                            embedding_function=ctx.embeddings,
                            persist_directory=str(base_dir),
                            client_settings=_chroma_client_settings(),
                        )
                        ctx.stores[f.file_id] = store
                        reused_files += 1
                        continue
                except Exception:
                    pass

            try:
                store = Chroma.from_documents(
                    docs,
                    ctx.embeddings,
                    ids=chunk_ids,
                    collection_name=collection_name,
                    persist_directory=str(base_dir),
                    client_settings=_chroma_client_settings(),
                )
                ctx.stores[f.file_id] = store
                indexed_new_files += 1
            except Exception as exc:
                logger.warning("Embeddings unavailable; falling back to lexical retrieval: %s", exc)
                ctx.metrics["embeddings_error"] = str(exc)
                ctx.metrics["embeddings_disabled"] = True
                ctx.embeddings = None
                break

        ctx.metrics["indexed_files"] = len(ctx.stores)
        ctx.metrics["indexed_reused_files"] = reused_files
        ctx.metrics["indexed_new_files"] = indexed_new_files
        return ctx


class PlannerAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        assert ctx.llm is not None

        per_file_topics: dict[str, list[str]] = {}
        per_file_quota = _per_file_quota(ctx)
        target = _planner_topics_target(per_file_quota)
        ctx.metrics["planner_topics_target"] = target
        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            chunks = ctx.normalized_chunks.get(f.file_id, [])
            sample = "\n\n".join(ch["text"][:600] for ch in chunks[:8])
            if not sample.strip():
                per_file_topics[f.file_id] = []
                continue

            prompt = (
                "Сформируй список тем по документу ниже.\n"
                f"Нужно ровно {target} тем.\n"
                "Темы короткие, существительные/словосочетания.\n"
                "Верни ТОЛЬКО JSON-объект вида {\"items\": [\"тема\", ...]} без Markdown.\n\n"
                f"ДОКУМЕНТ (ФРАГМЕНТЫ):\n{sample}"
            )
            raw = invoke(
                ctx.llm,
                prompt,
                should_cancel=ctx.should_cancel,
                metrics=ctx.metrics,
                operation="planner_topics",
            )
            topics = _parse_topic_list(
                ctx.llm,
                raw,
                f.file_name,
                should_cancel=ctx.should_cancel,
                metrics=ctx.metrics,
            )
            per_file_topics[f.file_id] = topics[:target]

        ctx.per_file_topics = per_file_topics
        ctx.metrics["topics_per_file"] = {k: len(v) for k, v in per_file_topics.items()}
        return ctx


class EvidenceAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        per_file_evidence: dict[str, list[dict[str, Any]]] = {}
        if ctx.stores:
            ctx.metrics.setdefault("retrieval_mode", "vector")
        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            store = ctx.stores.get(f.file_id)
            chunks = ctx.normalized_chunks.get(f.file_id, [])[: settings.rag_max_chunks]
            if not store:
                if not chunks:
                    per_file_evidence[f.file_id] = []
                    continue
                ctx.metrics.setdefault("retrieval_mode", "lexical")
                tokenized = [
                    (ch, set(normalize_text(str(ch.get("text", ""))).split()))
                    for ch in chunks
                ]
                topics = ctx.per_file_topics.get(f.file_id) or ["ключевые факты"]
                evidence_items: list[dict[str, Any]] = []
                for topic in topics:
                    if _cancelled(ctx):
                        return ctx
                    topic_tokens = set(normalize_text(topic).split())
                    if not topic_tokens:
                        selected = chunks[: settings.rag_top_k]
                    else:
                        scored = sorted(
                            tokenized,
                            key=lambda item: len(item[1] & topic_tokens),
                            reverse=True,
                        )
                        selected = [item[0] for item in scored if item[1] & topic_tokens][
                            : settings.rag_top_k
                        ]
                        if not selected:
                            selected = chunks[: settings.rag_top_k]
                    context = build_context_packet(selected)
                    evidence_items.append({"topic": topic, "context": context})
                per_file_evidence[f.file_id] = evidence_items
                continue
            retriever = store.as_retriever(search_kwargs={"k": settings.rag_top_k})
            topics = ctx.per_file_topics.get(f.file_id) or ["ключевые факты"]

            evidence_items: list[dict[str, Any]] = []
            for topic in topics:
                if _cancelled(ctx):
                    return ctx
                docs = retriever.invoke(topic)
                if not isinstance(docs, list):
                    docs = []
                docs = docs[: settings.rag_top_k]
                if not docs:
                    continue
                chunks = [
                    {
                        "text": d.page_content,
                        "source": d.metadata.get("source", f.file_name),
                        "index": d.metadata.get("chunk_index", 0),
                    }
                    for d in docs
                ]
                context = build_context_packet(chunks)
                evidence_items.append({"topic": topic, "context": context})
            per_file_evidence[f.file_id] = evidence_items
        ctx.per_file_evidence = per_file_evidence
        ctx.metrics["evidence_packets"] = sum(len(v) for v in per_file_evidence.values())
        return ctx


class QGenAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        assert ctx.llm is not None

        quota = _per_file_quota(ctx)
        target_raw = _qgen_target_raw(quota)
        ctx.metrics["qgen_target_raw_per_file"] = target_raw
        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            evidence_items = ctx.per_file_evidence.get(f.file_id, [])

            questions: list[dict[str, Any]] = []
            for ev in evidence_items:
                if _cancelled(ctx):
                    return ctx
                used_questions = [
                    q.get("question", "")
                    for q in questions
                    if isinstance(q.get("question", ""), str) and q.get("question")
                ][-6:]
                avoid_block = ""
                if used_questions:
                    avoid_block = "НЕ ПОВТОРЯЙ ЭТИ ИДЕИ:\n" + "\n".join(
                        f"- {q.strip()}" for q in used_questions if q.strip()
                    )

                topic = ev.get("topic", "")
                context = ev.get("context", "")
                if not context:
                    continue

                prompt = (
                    "Ты генерируешь обучающие вопросы по материалу.\n"
                    "Правила:\n"
                    "- Используй ТОЛЬКО информацию из КОНТЕКСТА.\n"
                    "- Для каждого вопроса добавь sources: список меток source из контекста.\n"
                    "- Типы: open, mcq, tf.\n"
                    "- mcq: ровно 4 варианта, correct_index 0..3.\n"
                    "- Язык: русский.\n"
                    "- Каждый вопрос должен покрывать НОВЫЙ факт; не повторяй идеи.\n"
                    f"- Нужно до {min(settings.rag_questions_per_topic, 6)} вопросов.\n"
                    "- Верни ТОЛЬКО JSON-объект вида {\"items\": [...]} без Markdown и пояснений.\n"
                    "Формат элементов:\n"
                    "{type, question, answer, options, correct_index, tags, sources, evidence}\n\n"
                    f"ТЕМА: {topic}\n"
                    f"СЛОЖНОСТЬ: {ctx.difficulty}\n\n"
                    f"{avoid_block}\n\nКОНТЕКСТ:\n{context}"
                )

                raw = invoke(
                    ctx.llm,
                    prompt,
                    should_cancel=ctx.should_cancel,
                    metrics=ctx.metrics,
                    operation="qgen",
                )
                data = _parse_qgen_payload(
                    ctx.llm,
                    raw,
                    f.file_name,
                    should_cancel=ctx.should_cancel,
                    metrics=ctx.metrics,
                )
                if not data:
                    continue

                for item in data:
                    if not isinstance(item, dict):
                        continue
                    question = (item.get("question") or "").strip()
                    if not question:
                        continue
                    item.setdefault("type", "open")
                    item.setdefault("tags", [])
                    item.setdefault("sources", [])
                    item.setdefault("evidence", [])
                    questions.append(item)

                if len(questions) >= target_raw:
                    break

            ctx.per_file_questions[f.file_id] = questions[:target_raw]
        ctx.metrics["raw_questions_total"] = sum(len(v) for v in ctx.per_file_questions.values())
        return ctx


class VerifierAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        for f in ctx.files:
            items = ctx.per_file_questions.get(f.file_id, [])
            cleaned: list[dict[str, Any]] = []
            for item in items:
                tags = item.get("tags") or []
                if isinstance(tags, str):
                    tags = [t for t in tags.split() if t]
                if not isinstance(tags, list):
                    tags = []
                sources = item.get("sources") or []
                if not sources:
                    tags.append("unverified")
                item["tags"] = tags
                cleaned.append(item)
            ctx.per_file_questions[f.file_id] = cleaned
        return ctx


class MixerAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        if _cancelled(ctx):
            return ctx
        all_items: list[dict[str, Any]] = []
        per_file_quota = max(1, ctx.requested_total // max(1, len(ctx.files)))

        for f in ctx.files:
            items = ctx.per_file_questions.get(f.file_id, [])
            all_items.extend(items[: per_file_quota * 2])

        if len(all_items) < ctx.requested_total * 2:
            for f in ctx.files:
                items = ctx.per_file_questions.get(f.file_id, [])
                all_items.extend(items[per_file_quota * 2 :])

        all_items = normalize_question_items(all_items, ctx.difficulty)
        before = len(all_items)
        all_items = cheap_dedupe(all_items)
        all_items = dedupe_questions(all_items)
        after = len(all_items)

        ctx.metrics["dedupe_drop_rate"] = (before - after) / max(1, before)

        if len(all_items) < ctx.requested_total:
            for f in ctx.files:
                items = normalize_question_items(ctx.per_file_questions.get(f.file_id, []), ctx.difficulty)
                for item in items:
                    all_items.append(item)
                    if len(all_items) >= ctx.requested_total:
                        break
                if len(all_items) >= ctx.requested_total:
                    break
            all_items = cheap_dedupe(all_items)
            all_items = dedupe_questions(all_items)

        all_items = [to_anki_qa(item) for item in all_items]

        ctx.metrics["final_count"] = min(ctx.requested_total, len(all_items))
        ctx.metrics["per_file_counts"] = {
            f.file_id: len(ctx.per_file_questions.get(f.file_id, [])) for f in ctx.files
        }
        ctx.metrics["_result"] = all_items[: ctx.requested_total]
        return ctx
