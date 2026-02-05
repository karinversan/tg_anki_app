from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.schemas.base import APIModel


class FileOut(APIModel):
    id: UUID
    topic_id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    deleted_at: datetime | None
