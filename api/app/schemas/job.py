from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.base import APIModel


class JobCreate(BaseModel):
    mode: str
    number_of_questions: int
    difficulty: str
    avoid_repeats: bool = True
    include_answers: bool = True


class JobOut(APIModel):
    id: UUID
    topic_id: UUID
    user_id: int
    mode: str
    status: str
    progress: int
    stage: str
    params_json: dict
    result_paths: dict | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
