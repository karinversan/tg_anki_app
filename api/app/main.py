from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import admin, auth, files, jobs, topics
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.cache import close_redis

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        llm_provider = (settings.llm_provider or "").strip().lower()
        if llm_provider == "gemini" and not (settings.gemini_api_key or os.getenv("GOOGLE_API_KEY")):
            logger.warning("GEMINI_API_KEY/GOOGLE_API_KEY is not set; gemini generation will fail.")
        if llm_provider == "openrouter" and not settings.openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY is not set; openrouter generation will fail.")
        if llm_provider in {"ollama", "local"}:
            logger.info("LLM provider is local (%s): %s", llm_provider, settings.local_llm_model)
        if llm_provider == "openrouter":
            logger.info("LLM provider is openrouter: %s", settings.openrouter_model)
        yield
        await close_redis()

    app = FastAPI(title="Telegram Anki API", lifespan=lifespan)

    @app.middleware("http")
    async def normalize_api_prefix(request: Request, call_next):
        path = request.scope.get("path", "")
        # Support reverse proxies that forward requests as /api/* without stripping the prefix.
        if path == "/api" or path.startswith("/api/"):
            normalized = path[4:] or "/"
            request.scope["path"] = normalized
            request.scope["raw_path"] = normalized.encode("utf-8")
        return await call_next(request)

    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"] ,
        allow_headers=["*"] ,
    )

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(topics.router)
    app.include_router(files.router)
    app.include_router(jobs.router)

    @app.get("/")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
