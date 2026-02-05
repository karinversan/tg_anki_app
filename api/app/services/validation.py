from __future__ import annotations

from dataclasses import dataclass

import filetype

from app.core.config import settings


ALLOWED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ALLOWED_EXT = {".pdf", ".txt", ".md", ".docx"}


@dataclass
class FileValidationResult:
    mime_type: str


def validate_file(filename: str, content: bytes) -> FileValidationResult:
    if not filename:
        raise ValueError("Missing filename")
    ext = filename.lower().rsplit(".", 1)
    if len(ext) == 1:
        raise ValueError("File extension is required")
    ext = f".{ext[1]}"
    if ext not in ALLOWED_EXT:
        raise ValueError("Unsupported file type")

    kind = filetype.guess(content)
    mime_type = kind.mime if kind else "text/plain"

    if mime_type not in ALLOWED_MIME:
        raise ValueError("Unsupported file MIME type")

    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError("File exceeds size limit")

    return FileValidationResult(mime_type=mime_type)
