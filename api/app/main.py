from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, files, jobs, topics
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.cache import close_redis

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not (settings.gemini_api_key or os.getenv("GOOGLE_API_KEY")):
            logger.warning("GEMINI_API_KEY/GOOGLE_API_KEY is not set; generation will fail until provided.")
        yield
        await close_redis()

    app = FastAPI(title="Telegram Anki API", lifespan=lifespan)

    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"] ,
        allow_headers=["*"] ,
    )

    app.include_router(auth.router)
    app.include_router(topics.router)
    app.include_router(files.router)
    app.include_router(jobs.router)

    @app.get("/")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
