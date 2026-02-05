from __future__ import annotations

import asyncio

from worker.celery_app import celery_app
from worker.job_runner import run_generation_job


@celery_app.task(name="worker.tasks.generate_questions")
def generate_questions(job_id: str) -> None:
    asyncio.run(run_generation_job(job_id))
