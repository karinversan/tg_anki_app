from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from langchain_core.documents import Document
from langchain_chroma import Chroma

from app.core.config import settings
from app.services.dedupe import dedupe_questions
from app.services.qa.clients import build_embeddings, build_llm, invoke
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


def _parse_qgen_payload(
    llm: Any,
    raw: str,
    file_name: str,
    should_cancel: Any | None = None,
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
            raw = invoke(llm, repair_prompt, should_cancel=should_cancel)
    logger.warning("QGenAgent parse failed (%s): %s", file_name, last_exc)
    return []


def _parse_topic_list(
    llm: Any,
    raw: str,
    file_name: str,
    should_cancel: Any | None = None,
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
            raw = invoke(llm, repair_prompt, should_cancel=should_cancel)
    logger.warning("PlannerAgent: topics parse failed (%s): %s", file_name, last_exc)
    return []


class SetupAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        ctx.llm = ctx.llm or build_llm()
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

        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            if ctx.embeddings is None:
                break
            chunks = ctx.normalized_chunks.get(f.file_id, [])
            if not chunks:
                continue
            hashes = [chunk_hash(f.file_id, ch) for ch in chunks]
            content_hash = hashlib.sha256("".join(hashes).encode("utf-8")).hexdigest()
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

            try:
                store = Chroma.from_documents(
                    docs,
                    ctx.embeddings,
                    collection_name=collection_name,
                    persist_directory=str(base_dir),
                )
                ctx.stores[f.file_id] = store
            except Exception as exc:
                logger.warning("Embeddings unavailable; falling back to lexical retrieval: %s", exc)
                ctx.metrics["embeddings_error"] = str(exc)
                ctx.metrics["embeddings_disabled"] = True
                ctx.embeddings = None
                break

        ctx.metrics["indexed_files"] = len(ctx.stores)
        return ctx


class PlannerAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        assert ctx.llm is not None

        per_file_topics: dict[str, list[str]] = {}
        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            chunks = ctx.normalized_chunks.get(f.file_id, [])
            sample = "\n\n".join(ch["text"][:600] for ch in chunks[:8])
            if not sample.strip():
                per_file_topics[f.file_id] = []
                continue

            target = max(3, min(10, ctx.requested_total // max(1, len(ctx.files)) // 3 + 3))
            prompt = (
                "Сформируй список тем по документу ниже.\n"
                f"Нужно ровно {target} тем.\n"
                "Темы короткие, существительные/словосочетания.\n"
                "Верни ТОЛЬКО JSON-объект вида {\"items\": [\"тема\", ...]} без Markdown.\n\n"
                f"ДОКУМЕНТ (ФРАГМЕНТЫ):\n{sample}"
            )
            raw = invoke(ctx.llm, prompt, should_cancel=ctx.should_cancel)
            topics = _parse_topic_list(ctx.llm, raw, f.file_name, should_cancel=ctx.should_cancel)
            per_file_topics[f.file_id] = topics[:target]

        ctx.per_file_topics = per_file_topics
        ctx.metrics["topics_per_file"] = {k: len(v) for k, v in per_file_topics.items()}
        return ctx


class EvidenceAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        per_file_evidence: dict[str, list[dict[str, Any]]] = {}
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
                docs = retriever.get_relevant_documents(topic)[: settings.rag_top_k]
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
        return ctx


class QGenAgent(Agent):
    def run(self, ctx: QAContext) -> QAContext:
        assert ctx.llm is not None

        for f in ctx.files:
            if _cancelled(ctx):
                return ctx
            evidence_items = ctx.per_file_evidence.get(f.file_id, [])
            quota = max(2, ctx.requested_total // max(1, len(ctx.files)))
            target_raw = quota * 3

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

                raw = invoke(ctx.llm, prompt, should_cancel=ctx.should_cancel)
                data = _parse_qgen_payload(ctx.llm, raw, f.file_name, should_cancel=ctx.should_cancel)
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
