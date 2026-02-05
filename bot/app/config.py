from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    bot_token: str = "dev-bot-token"
    web_base_url: str = "http://localhost:5173"


settings = Settings()
