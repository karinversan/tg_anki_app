from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl

from jose import JWTError, jwt

from app.core.config import settings


def _get_secret_key() -> bytes:
    return settings.jwt_secret.encode("utf-8")


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    exp_minutes = expires_minutes or settings.jwt_expires_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=exp_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, _get_secret_key(), algorithm="HS256")


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=["HS256"])
    except JWTError:
        return None
    return payload.get("sub")


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict[str, Any]:
    data = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = data.pop("hash", "")
    auth_date = int(data.get("auth_date", "0"))
    if not received_hash:
        raise ValueError("Missing hash in initData")
    if auth_date <= 0:
        raise ValueError("Missing auth_date in initData")
    if time.time() - auth_date > max_age_seconds:
        raise ValueError("initData is too old")

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    digest = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(digest, received_hash):
        raise ValueError("Invalid initData hash")

    user_raw = data.get("user")
    user = None
    if user_raw:
        import json

        user = json.loads(user_raw)

    return {"data": data, "user": user}


def decode_encryption_key() -> bytes:
    raw = settings.encryption_key_base64
    try:
        return base64.b64decode(raw)
    except Exception as exc:
        raise ValueError("Invalid ENCRYPTION_KEY_BASE64") from exc
