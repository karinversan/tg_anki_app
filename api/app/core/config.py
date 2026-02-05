from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    app_env: str = "development"
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/tg_anki"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    jwt_secret: str = "dev-secret"
    jwt_expires_minutes: int = 15

    bot_token: str = "dev-bot-token"

    storage_path: str = "./data"
    encryption_key_base64: str = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_required: bool = False

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_embedding_model: str = "models/embedding-001"
    chroma_path: str = "./data/chroma"
    rag_top_k: int = 5
    rag_min_topics: int = 3
    rag_max_topics: int = 12
    rag_max_chunks: int = 400
    rag_claims_per_topic: int = 12
    rag_questions_per_topic: int = 6
    rag_judge_batch_size: int = 8
    rag_use_embeddings: bool = True
    embedding_provider: str = "gemini"
    embedding_init_backoff_seconds: int = 300
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    hf_home: str = "./data/huggingface"
    hf_offline: bool = False
    filter_generic_answers: bool = True
    generic_answer_patterns: str = ""
    filter_unrelated_content: bool = True
    unrelated_content_patterns: str = ""

    llm_json_retries: int = 1
    llm_json_repair_max_chars: int = 6000

    job_webhook_url: str | None = None

    cors_origins: str = "http://localhost:5173"

    rate_limit_generations_per_minute: int = 3
    rate_limit_uploads_per_minute: int = 10
    max_files_per_topic: int = 10
    max_file_size_mb: int = 20


settings = Settings()
