from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

import httpx

from langchain_core.embeddings import Embeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.core.config import settings

logger = logging.getLogger(__name__)
_EMBEDDINGS_CACHE: Embeddings | None = None
_EMBEDDINGS_LAST_ERROR: str | None = None
_EMBEDDINGS_LAST_ATTEMPT: float = 0.0


def gemini_client() -> ChatGoogleGenerativeAI:
    api_key = settings.gemini_api_key or os.getenv("GOOGLE_API_KEY")
    kwargs = {
        "model": settings.gemini_model,
        "temperature": 0.3,
        "response_mime_type": "application/json",
    }
    if api_key:
        kwargs["google_api_key"] = api_key
    try:
        return ChatGoogleGenerativeAI(**kwargs)
    except TypeError:
        kwargs.pop("response_mime_type", None)
        return ChatGoogleGenerativeAI(**kwargs)


def gemini_embeddings() -> GoogleGenerativeAIEmbeddings:
    api_key = settings.gemini_api_key or os.getenv("GOOGLE_API_KEY")
    kwargs = {"model": settings.gemini_embedding_model}
    if api_key:
        kwargs["google_api_key"] = api_key
    return GoogleGenerativeAIEmbeddings(**kwargs)


class OpenRouterChatClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        referer: str | None = None,
        app_name: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        base = base_url.rstrip("/")
        self._url = f"{base}/chat/completions"
        self._model = model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if referer:
            self._headers["HTTP-Referer"] = referer
        if app_name:
            self._headers["X-Title"] = app_name
        self._client = httpx.Client(timeout=timeout)

    def invoke(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        response = self._client.post(self._url, headers=self._headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text}") from exc
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenRouter returned no choices: {data}")
        message = choices[0].get("message") or {}
        return str(message.get("content", "")).strip()


class LocalSentenceTransformerEmbeddings(Embeddings):
    def __init__(self, model_name: str, cache_folder: str | None = None, local_files_only: bool = False) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - import error surfaced at runtime
            raise RuntimeError(
                "sentence-transformers is required for local embeddings. Install it and rebuild images."
            ) from exc
        kwargs: dict[str, Any] = {}
        if cache_folder:
            kwargs["cache_folder"] = cache_folder
        if local_files_only:
            kwargs["local_files_only"] = True
        try:
            self._model = SentenceTransformer(model_name, **kwargs)
        except TypeError:
            kwargs.pop("local_files_only", None)
            self._model = SentenceTransformer(model_name, **kwargs)

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return [vec.tolist() for vec in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(
            [text],
            show_progress_bar=False,
            normalize_embeddings=True,
        )[0]
        return vector.tolist()


def local_embeddings() -> Embeddings:
    global _EMBEDDINGS_CACHE, _EMBEDDINGS_LAST_ERROR, _EMBEDDINGS_LAST_ATTEMPT
    if _EMBEDDINGS_CACHE is not None:
        return _EMBEDDINGS_CACHE
    if _EMBEDDINGS_LAST_ERROR:
        if time.time() - _EMBEDDINGS_LAST_ATTEMPT < settings.embedding_init_backoff_seconds:
            raise RuntimeError(_EMBEDDINGS_LAST_ERROR)
    _EMBEDDINGS_LAST_ATTEMPT = time.time()
    model_name = settings.local_embedding_model
    cache_folder = str(Path(settings.hf_home).expanduser())
    if cache_folder:
        Path(cache_folder).mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", cache_folder)
        os.environ.setdefault("TRANSFORMERS_CACHE", cache_folder)
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", cache_folder)
    try:
        embeddings = LocalSentenceTransformerEmbeddings(
            model_name,
            cache_folder=cache_folder,
            local_files_only=settings.hf_offline,
        )
    except Exception as exc:
        _EMBEDDINGS_LAST_ERROR = str(exc)
        raise
    _EMBEDDINGS_LAST_ERROR = None
    _EMBEDDINGS_CACHE = embeddings
    return embeddings


def openrouter_client() -> OpenRouterChatClient:
    api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required when llm_provider=openrouter")
    referer = settings.openrouter_http_referer or settings.web_base_url
    app_name = settings.openrouter_app_name
    return OpenRouterChatClient(
        api_key=api_key,
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        referer=referer,
        app_name=app_name,
    )


def build_llm() -> Any:
    provider = (settings.llm_provider or "gemini").strip().lower()
    if provider == "openrouter":
        return openrouter_client()
    return gemini_client()


def build_embeddings() -> Embeddings:
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider == "local":
        return local_embeddings()
    return gemini_embeddings()


def invoke(llm: Any, prompt: str, attempts: int = 3) -> str:
    last: Exception | None = None
    retry_re = re.compile(r"retry(?:_delay)?[^0-9]*([0-9]+(?:\\.[0-9]+)?)", re.IGNORECASE)
    for _ in range(attempts):
        try:
            msg = llm.invoke(prompt)
            content = getattr(msg, "content", msg)
            if isinstance(content, (dict, list)):
                return json.dumps(content, ensure_ascii=False)
            return str(content)
        except Exception as exc:
            last = exc
            logger.warning("LLM invoke failed: %s", exc)
            wait_seconds = 0.0
            match = retry_re.search(str(exc))
            if match:
                try:
                    wait_seconds = float(match.group(1))
                except ValueError:
                    wait_seconds = 0.0
            if wait_seconds > 0:
                time.sleep(min(wait_seconds, 60.0))
    raise RuntimeError(f"LLM invoke failed after {attempts} attempts: {last}")
