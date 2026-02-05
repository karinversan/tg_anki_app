from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.base import APIModel


class TopicCreate(BaseModel):
    title: str


class TopicUpdate(BaseModel):
    title: str


class TopicOut(APIModel):
    id: UUID
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime
    file_count: int = 0
