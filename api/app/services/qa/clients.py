from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
from typing import Any

import httpx
from langchain_core.embeddings import Embeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.core.config import settings

logger = logging.getLogger(__name__)


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


class LLMMessage:
    def __init__(
        self,
        content: str,
        *,
        response_metadata: dict[str, Any] | None = None,
        usage_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.response_metadata = response_metadata or {}
        self.usage_metadata = usage_metadata or {}


class OpenRouterClient:
    provider = "openrouter"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float,
        timeout_seconds: int,
        app_name: str,
    ) -> None:
        self.model_name = model
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = max(5, timeout_seconds)
        self._payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        self._app_name = app_name.strip() or "Telegram Anki"

    def invoke(self, prompt: str) -> LLMMessage:
        payload = dict(self._payload)
        payload["messages"] = [{"role": "user", "content": prompt}]
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.web_base_url,
            "X-Title": self._app_name,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(self._url, json=payload, headers=headers)
                if response.status_code == 400 and "response_format" in response.text.lower():
                    payload.pop("response_format", None)
                    response = client.post(self._url, json=payload, headers=headers)
        except Exception as exc:
            raise RuntimeError("OpenRouter request failed (network). Check connectivity from worker container.") from exc

        if response.status_code >= 400:
            detail = ""
            try:
                data = response.json()
                if isinstance(data, dict):
                    error = data.get("error")
                    if isinstance(error, dict):
                        detail = str(error.get("message") or error.get("code") or "")
                    elif error is not None:
                        detail = str(error)
            except Exception:
                detail = response.text.strip()
            detail = detail or "Unknown error"
            if response.status_code == 429:
                retry_after_raw = response.headers.get("Retry-After") or ""
                try:
                    retry_after = max(1.0, float(retry_after_raw))
                except Exception:
                    retry_after = 10.0
                raise RuntimeError(
                    f"OpenRouter rate limit (429): {detail}. retry_delay={retry_after}"
                )
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {detail}")

        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or "OpenRouter error")
            else:
                message = str(error)
            raise RuntimeError(message)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter returned empty choices")
        message = choices[0].get("message", {})
        content_raw = message.get("content")
        content = _extract_openrouter_content(content_raw)
        if not content.strip():
            raise RuntimeError("OpenRouter returned empty response")

        usage_raw = data.get("usage")
        usage = {
            "prompt_tokens": _safe_number(usage_raw.get("prompt_tokens")) if isinstance(usage_raw, dict) else None,
            "completion_tokens": _safe_number(usage_raw.get("completion_tokens")) if isinstance(usage_raw, dict) else None,
            "total_tokens": _safe_number(usage_raw.get("total_tokens")) if isinstance(usage_raw, dict) else None,
        }
        response_meta = {
            "model": data.get("model") or self.model_name,
            "provider": "openrouter",
        }
        return LLMMessage(
            content,
            response_metadata=response_meta,
            usage_metadata=usage,
        )


def openrouter_client() -> OpenRouterClient:
    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return OpenRouterClient(
        model=settings.openrouter_model,
        api_key=api_key,
        base_url=settings.openrouter_base_url,
        temperature=settings.openrouter_temperature,
        timeout_seconds=settings.openrouter_request_timeout_seconds,
        app_name=settings.openrouter_app_name,
    )


def build_llm() -> Any:
    provider = (settings.llm_provider or "openrouter").strip().lower()
    if provider == "openrouter":
        return openrouter_client()
    if provider == "gemini":
        return gemini_client()
    raise ValueError(f"Unsupported llm provider: {settings.llm_provider}. Use openrouter or gemini.")


def build_embeddings() -> Embeddings:
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider != "gemini":
        logger.warning("Unsupported embedding provider '%s'; falling back to gemini.", provider)
    return gemini_embeddings()


def llm_descriptor(llm: Any) -> dict[str, Any]:
    system_name = platform.system().lower()
    machine = platform.machine().lower()
    descriptor: dict[str, Any] = {
        "provider": getattr(llm, "provider", "gemini"),
        "platform": system_name,
        "machine": machine,
    }
    if isinstance(llm, OpenRouterClient):
        descriptor["model"] = llm.model_name
        descriptor["acceleration"] = "remote"
    else:
        descriptor["model"] = settings.gemini_model
        descriptor["acceleration"] = "remote"
    return descriptor


def _extract_openrouter_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        return "\n".join(part.strip() for part in text_parts if part and part.strip()).strip()
    return str(content or "").strip()


def _should_cancel(should_cancel: Any | None) -> bool:
    return bool(should_cancel and should_cancel())


def _sleep_with_cancel(seconds: float, should_cancel: Any | None) -> None:
    if seconds <= 0:
        return
    if should_cancel is None:
        time.sleep(seconds)
        return
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _should_cancel(should_cancel):
            return
        time.sleep(min(0.5, max(0.0, deadline - time.time())))


def _safe_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _duration_to_seconds(value: Any) -> float | None:
    number = _safe_number(value)
    if number is None:
        return None
    if number > 10_000:
        return number / 1_000_000_000.0
    return number


def _extract_usage(msg: Any) -> dict[str, float]:
    usage: dict[str, float] = {}
    usage_meta = getattr(msg, "usage_metadata", None)
    if isinstance(usage_meta, dict):
        prompt_tokens = _safe_number(
            usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens")
        )
        output_tokens = _safe_number(
            usage_meta.get("output_tokens") or usage_meta.get("completion_tokens")
        )
        total_tokens = _safe_number(usage_meta.get("total_tokens"))
        if prompt_tokens is not None:
            usage["prompt_tokens"] = prompt_tokens
        if output_tokens is not None:
            usage["output_tokens"] = output_tokens
        if total_tokens is not None:
            usage["total_tokens"] = total_tokens
    response_meta = getattr(msg, "response_metadata", None)
    if isinstance(response_meta, dict):
        if "prompt_tokens" not in usage:
            prompt_tokens = _safe_number(
                response_meta.get("prompt_eval_count") or response_meta.get("prompt_tokens")
            )
            if prompt_tokens is not None:
                usage["prompt_tokens"] = prompt_tokens
        if "output_tokens" not in usage:
            output_tokens = _safe_number(
                response_meta.get("eval_count") or response_meta.get("output_tokens")
            )
            if output_tokens is not None:
                usage["output_tokens"] = output_tokens
        eval_duration = _duration_to_seconds(
            response_meta.get("eval_duration_sec") or response_meta.get("eval_duration")
        )
        if eval_duration is not None:
            usage["eval_duration_sec"] = eval_duration
    return usage


def _record_llm_success(
    metrics: dict[str, Any],
    *,
    operation: str | None,
    latency_sec: float,
    prompt: str,
    response_text: str,
    usage: dict[str, float],
) -> None:
    llm_metrics = metrics.setdefault("llm", {})
    llm_metrics["calls_total"] = int(llm_metrics.get("calls_total", 0)) + 1
    llm_metrics["latency_total_sec"] = float(llm_metrics.get("latency_total_sec", 0.0)) + latency_sec
    llm_metrics["latency_max_sec"] = max(float(llm_metrics.get("latency_max_sec", 0.0)), latency_sec)
    llm_metrics["latency_avg_sec"] = llm_metrics["latency_total_sec"] / max(1, llm_metrics["calls_total"])
    llm_metrics["prompt_chars_total"] = int(llm_metrics.get("prompt_chars_total", 0)) + len(prompt)
    llm_metrics["response_chars_total"] = int(llm_metrics.get("response_chars_total", 0)) + len(response_text)
    if operation:
        counts = llm_metrics.setdefault("operation_counts", {})
        counts[operation] = int(counts.get(operation, 0)) + 1

    if usage.get("prompt_tokens") is not None:
        llm_metrics["prompt_tokens_total"] = float(llm_metrics.get("prompt_tokens_total", 0.0)) + usage["prompt_tokens"]
    if usage.get("output_tokens") is not None:
        llm_metrics["output_tokens_total"] = float(llm_metrics.get("output_tokens_total", 0.0)) + usage["output_tokens"]
    if usage.get("total_tokens") is not None:
        llm_metrics["total_tokens_total"] = float(llm_metrics.get("total_tokens_total", 0.0)) + usage["total_tokens"]
    eval_duration = usage.get("eval_duration_sec")
    output_tokens = usage.get("output_tokens")
    if eval_duration and output_tokens:
        speed = output_tokens / max(eval_duration, 1e-9)
        llm_metrics["decode_tokens_per_sec_avg"] = (
            (
                float(llm_metrics.get("decode_tokens_per_sec_avg", 0.0))
                * max(0, llm_metrics["calls_total"] - 1)
            )
            + speed
        ) / llm_metrics["calls_total"]


def invoke(
    llm: Any,
    prompt: str,
    attempts: int = 4,
    should_cancel: Any | None = None,
    metrics: dict[str, Any] | None = None,
    operation: str | None = None,
) -> str:
    last: Exception | None = None
    retry_re = re.compile(r"retry(?:_delay)?[^0-9]*([0-9]+(?:\\.[0-9]+)?)", re.IGNORECASE)
    provider = str(getattr(llm, "provider", "") or "").strip().lower()
    effective_attempts = max(1, attempts)
    total_retry_budget_sec: float | None = None
    if provider == "openrouter":
        effective_attempts = min(effective_attempts, 3)
        total_retry_budget_sec = max(30.0, float(settings.openrouter_request_timeout_seconds) * 1.5)
    started_total = time.perf_counter()
    for attempt in range(effective_attempts):
        if _should_cancel(should_cancel):
            return ""
        started = time.perf_counter()
        try:
            msg = llm.invoke(prompt)
            content = getattr(msg, "content", msg)
            if isinstance(content, (dict, list)):
                response_text = json.dumps(content, ensure_ascii=False)
            else:
                response_text = str(content)
            if metrics is not None:
                _record_llm_success(
                    metrics,
                    operation=operation,
                    latency_sec=time.perf_counter() - started,
                    prompt=prompt,
                    response_text=response_text,
                    usage=_extract_usage(msg),
                )
            return response_text
        except Exception as exc:
            last = exc
            logger.warning(
                "LLM invoke failed (provider=%s, operation=%s, attempt=%s/%s): %s",
                provider or "unknown",
                operation or "default",
                attempt + 1,
                effective_attempts,
                exc,
            )
            lowered = str(exc).lower()
            if "insufficient credits" in lowered or "payment required" in lowered:
                break
            if _should_cancel(should_cancel):
                return ""
            wait_seconds = 0.0
            match = retry_re.search(str(exc))
            if match:
                try:
                    wait_seconds = float(match.group(1))
                except ValueError:
                    wait_seconds = 0.0
            if not wait_seconds:
                if "network connection lost" in lowered or " 502" in lowered or "code 502" in lowered:
                    wait_seconds = min(2 ** (attempt + 1), 20.0)
                elif "rate limit" in lowered or "429" in lowered:
                    wait_seconds = min(5.0 * (attempt + 1), 60.0)
            if metrics is not None:
                llm_metrics = metrics.setdefault("llm", {})
                llm_metrics["calls_failed"] = int(llm_metrics.get("calls_failed", 0)) + 1
                if attempt < effective_attempts - 1:
                    llm_metrics["retries_total"] = int(llm_metrics.get("retries_total", 0)) + 1
            if wait_seconds > 0:
                _sleep_with_cancel(min(wait_seconds, 60.0), should_cancel)
            if total_retry_budget_sec is not None:
                elapsed_total = time.perf_counter() - started_total
                if elapsed_total >= total_retry_budget_sec:
                    break
        if _should_cancel(should_cancel):
            return ""
    if total_retry_budget_sec is not None and provider == "openrouter":
        elapsed_total = time.perf_counter() - started_total
        raise RuntimeError(
            f"LLM invoke failed after {effective_attempts} attempts in {elapsed_total:.1f}s: {last}"
        )
    raise RuntimeError(f"LLM invoke failed after {effective_attempts} attempts: {last}")
