from __future__ import annotations

def chunk_text(text: str, min_tokens: int = 250, max_tokens: int = 450, overlap: int = 60) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    target = max_tokens
    while start < len(words):
        end = min(start + target, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end == len(words):
            break
        start = max(0, end - overlap)
    return chunks
