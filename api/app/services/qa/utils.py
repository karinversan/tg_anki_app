from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

from app.core.config import settings
from app.services.dedupe import dedupe_questions

_UNRELATED_LINE_PATTERNS = [
    r"(?i)\b(password|passcode|парол[ья])\b\s*[:=]",
    r"(?i)\b(api[-_ ]?key|apikey|access[-_ ]?key|secret|token|bearer)\b\s*[:=]",
    r"(?i)\b(client[_-]?secret|client[_-]?id)\b\s*[:=]",
    r"(?i)-----BEGIN (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----",
    r"(?i)\b(sk-[a-z0-9]{10,})\b",
    r"(?i)\b(ssh-rsa|ssh-ed25519)\b",
    r"(?i)\b(privacy policy|cookie policy|terms of service|terms and conditions)\b",
    r"(?i)\b(политика конфиденциальности|пользовательское соглашение|условия использования|оферта)\b",
    r"(?i)\b(инн|огрн|кпп|снилс|паспорт|юридический адрес|банковские реквизиты|расчетный счет|корреспондентский счет|бик)\b",
]
_COMPILED_UNRELATED: list[re.Pattern] | None = None
_COMPILED_GENERIC: list[re.Pattern] | None = None

_GENERIC_ANSWER_PATTERNS = [
    r"^константа$",
    r"^константный ответ$",
    r"^оптимальный ответ$",
    r"^оптимальный константный ответ$",
    r"^среднее$",
    r"^среднее значение$",
    r"^среднее арифметическое$",
]

_TF_PREFIXES = (
    "верно ли",
    "правда ли",
    "всегда ли",
    "может ли",
    "является ли",
)


def _unrelated_patterns() -> list[re.Pattern]:
    global _COMPILED_UNRELATED
    if _COMPILED_UNRELATED is not None:
        return _COMPILED_UNRELATED
    patterns = list(_UNRELATED_LINE_PATTERNS)
    extra = settings.unrelated_content_patterns.strip()
    if extra:
        for item in re.split(r"[;\n]+", extra):
            cleaned = item.strip()
            if cleaned:
                patterns.append(cleaned)
    _COMPILED_UNRELATED = [re.compile(p) for p in patterns]
    return _COMPILED_UNRELATED


def _generic_patterns() -> list[re.Pattern]:
    global _COMPILED_GENERIC
    if _COMPILED_GENERIC is not None:
        return _COMPILED_GENERIC
    patterns = list(_GENERIC_ANSWER_PATTERNS)
    extra = settings.generic_answer_patterns.strip()
    if extra:
        for item in re.split(r"[;\n]+", extra):
            cleaned = item.strip()
            if cleaned:
                patterns.append(cleaned)
    _COMPILED_GENERIC = [re.compile(p) for p in patterns]
    return _COMPILED_GENERIC


def filter_unrelated_text(text: str) -> tuple[str, int]:
    if not settings.filter_unrelated_content:
        return text, 0
    patterns = _unrelated_patterns()
    if not patterns:
        return text, 0
    removed = 0
    kept: list[str] = []
    for line in text.splitlines():
        if any(p.search(line) for p in patterns):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed


def normalize_text(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return text


def starts_with_tf_prefix(question: str) -> bool:
    if not question:
        return False
    cleaned = question.strip().lower()
    cleaned = re.sub(r"^[^a-zа-я0-9]+", "", cleaned)
    return any(cleaned.startswith(prefix) for prefix in _TF_PREFIXES)


def is_generic_answer(answer: str, question: str) -> bool:
    if not settings.filter_generic_answers:
        return False
    answer_norm = normalize_text(answer)
    if not answer_norm:
        return True
    if answer_norm in {"верно", "неверно"}:
        return False
    patterns = _generic_patterns()
    if any(p.fullmatch(answer_norm) for p in patterns):
        question_norm = normalize_text(question)
        if len(question_norm) >= 60 and not re.search(r"\b(mse|rmse|mae|r2|констант|средн)\b", question_norm):
            return True
    return False


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```", re.IGNORECASE)


def extract_first_json(text: str) -> str | None:
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()

    s = text.strip().lstrip("\ufeff")
    if s.startswith("[") or s.startswith("{"):
        return s

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(text[idx:])
            return text[idx : idx + end]
        except Exception:
            continue
    return None


def safe_json_loads(text: str) -> Any:
    raw = extract_first_json(text)
    if not raw:
        raise ValueError("No JSON found in model response")
    raw = re.sub(r",\s*([\]\}])", r"\1", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(raw)
    except Exception as exc:
        raise ValueError("Invalid JSON in model response") from exc


def normalize_chunks(
    chunks: list[dict[str, Any]] | list[str],
    source_name: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not chunks:
        return out
    if isinstance(chunks[0], dict):
        for i, c in enumerate(chunks):
            text = str(c.get("text", "")).strip()
            if not text:
                continue
            text, _ = filter_unrelated_text(text)
            if not text:
                continue
            out.append({"text": text, "source": c.get("source") or source_name, "index": c.get("index", i)})
        return out

    for i, text in enumerate(chunks):
        cleaned = str(text).strip()
        if not cleaned:
            continue
        cleaned, _ = filter_unrelated_text(cleaned)
        if not cleaned:
            continue
        out.append({"text": cleaned, "source": source_name, "index": i})
    return out


def chunk_hash(file_id: str, chunk: dict[str, Any]) -> str:
    payload = f"{file_id}|{chunk.get('source')}|{chunk.get('index')}|{chunk.get('text')}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_context_packet(chunks: list[dict[str, Any]], max_chars: int = 1600) -> str:
    parts = []
    for i, ch in enumerate(chunks, start=1):
        src = ch.get("source", "unknown")
        idx = ch.get("index", i)
        text = str(ch.get("text", ""))[:max_chars].strip()
        parts.append(f"[{i}] source: {src}#chunk{idx}\n{text}")
    return "\n\n".join(parts)


def to_anki_qa(item: dict[str, Any]) -> dict[str, Any]:
    q_type = str(item.get("type") or "open").lower()
    question = str(item.get("question") or "").strip()
    answer = str(item.get("answer") or "").strip()
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.split() if t]
    elif not isinstance(tags, list):
        tags = []

    if q_type == "mcq":
        options = item.get("options") or []
        correct_index = item.get("correct_index")
        if isinstance(correct_index, str) and correct_index.isdigit():
            correct_index = int(correct_index)
        if isinstance(options, list) and isinstance(correct_index, int) and 0 <= correct_index < len(options):
            candidate = str(options[correct_index]).strip()
            if candidate:
                answer = candidate
        item.pop("options", None)
        item.pop("correct_index", None)
        item["type"] = "open"
        tags = [t for t in tags if t != "mcq"]
        if "open" not in tags:
            tags.append("open")
    elif q_type == "tf":
        item["type"] = "open"
        if question and not starts_with_tf_prefix(question):
            question = f"Верно ли, что {question.rstrip('?')}?"
        answer_lower = answer.lower()
        if answer_lower in {"true", "верно", "истина", "да"}:
            answer = "Верно"
        elif answer_lower in {"false", "неверно", "ложь", "нет"}:
            answer = "Неверно"
        tags = [t for t in tags if t != "tf"]
        if "open" not in tags:
            tags.append("open")

    item["question"] = question
    item["answer"] = answer
    item["tags"] = tags
    return item


def cheap_dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for item in items:
        question = normalize_text(str(item.get("question", "")))
        if not question or question in seen:
            continue
        seen.add(question)
        out.append(item)
    return out


def normalize_question_items(items: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        q_type = str(item.get("type", "open")).lower().strip()
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [x for x in tags.split() if x]
        if not isinstance(tags, list):
            tags = []
        sources = item.get("sources") or []
        if isinstance(sources, str):
            sources = [x.strip() for x in sources.split(",") if x.strip()]
        evidence = item.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]

        rec: dict[str, Any] = {
            "type": q_type,
            "question": question,
            "answer": str(item.get("answer", "")).strip(),
            "difficulty": item.get("difficulty") or difficulty,
            "tags": [str(x).lower().strip() for x in tags if str(x).strip()],
            "sources": sources,
            "evidence": evidence,
            "options": item.get("options") if isinstance(item.get("options"), list) else None,
            "correct_index": item.get("correct_index"),
        }

        if rec["type"] == "mcq":
            opts = rec.get("options") or []
            if len(opts) != 4:
                rec["type"] = "open"
                rec["options"] = None
                rec["correct_index"] = None
            else:
                ci = rec.get("correct_index")
                if isinstance(ci, str) and ci.isdigit():
                    ci = int(ci)
                if not isinstance(ci, int) or not (0 <= ci < 4):
                    rec["type"] = "open"
                    rec["options"] = None
                    rec["correct_index"] = None

        if is_generic_answer(rec["answer"], rec["question"]):
            continue

        out.append(rec)
    return out


def merge_per_file_outputs(outputs: list[list[dict]], requested_total: int) -> list[dict]:
    merged: list[dict] = []
    for items in outputs:
        merged.extend(items)
    deduped = dedupe_questions(merged)
    if len(deduped) < requested_total:
        deduped.extend(merged)
    return deduped[:requested_total]
