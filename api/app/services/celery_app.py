from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "tg_anki",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.task_default_queue = "celery"
