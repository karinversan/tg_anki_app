from __future__ import annotations

import hashlib
import re
from typing import Iterable


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return text


def simhash(text: str) -> int:
    tokens = normalize(text).split()
    if not tokens:
        return 0
    bits = [0] * 64
    for token in tokens:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            bit = 1 if h & (1 << i) else -1
            bits[i] += bit
    value = 0
    for i, bit in enumerate(bits):
        if bit > 0:
            value |= 1 << i
    return value


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _token_set(text: str) -> set[str]:
    tokens = normalize(text).split()
    return {t for t in tokens if t}


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    compact = normalize(text).replace(" ", "")
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def dedupe_questions(questions: list[dict], max_distance: int = 3) -> list[dict]:
    seen: list[tuple[int, str, set[str], set[str]]] = []
    unique: list[dict] = []
    for item in questions:
        q_text = item.get("question", "")
        norm = normalize(q_text)
        if not norm:
            continue
        h = simhash(norm)
        tokens = _token_set(norm)
        ngrams = _char_ngrams(norm)
        is_dupe = False
        for existing_hash, existing_norm, existing_tokens, existing_ngrams in seen:
            if hamming_distance(h, existing_hash) <= max_distance:
                if norm == existing_norm:
                    is_dupe = True
                    break
                if _jaccard(tokens, existing_tokens) >= 0.85:
                    is_dupe = True
                    break
                if _jaccard(ngrams, existing_ngrams) >= 0.9:
                    is_dupe = True
                    break
        if not is_dupe:
            seen.append((h, norm, tokens, ngrams))
            unique.append(item)
    return unique
