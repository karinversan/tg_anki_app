from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.security import decode_encryption_key


def ensure_storage_path() -> Path:
    path = Path(settings.storage_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def encrypt_bytes(data: bytes) -> tuple[bytes, str, str]:
    key = decode_encryption_key()
    if len(key) != 32:
        raise ValueError("ENCRYPTION_KEY_BASE64 must decode to 32 bytes")
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    encrypted = aesgcm.encrypt(nonce, data, None)
    tag = encrypted[-16:]
    return encrypted, nonce.hex(), tag.hex()


def decrypt_bytes(encrypted: bytes, nonce_hex: str) -> bytes:
    key = decode_encryption_key()
    if len(key) != 32:
        raise ValueError("ENCRYPTION_KEY_BASE64 must decode to 32 bytes")
    nonce = bytes.fromhex(nonce_hex)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, encrypted, None)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def topic_storage_dir(topic_id: str) -> Path:
    base = ensure_storage_path()
    path = base / "topics" / topic_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_storage_dir(topic_id: str) -> Path:
    base = ensure_storage_path()
    path = base / "exports" / topic_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_encrypted_file(topic_id: str, filename: str, data: bytes) -> tuple[str, str, str, int, str]:
    encrypted, nonce_hex, tag_hex = encrypt_bytes(data)
    storage_dir = topic_storage_dir(topic_id)
    suffix = Path(filename).suffix or ".bin"
    storage_path = storage_dir / f"{uuid4().hex}{suffix}"
    storage_path.write_bytes(encrypted)
    return str(storage_path), nonce_hex, tag_hex, len(data), sha256_hex(data)


def read_encrypted_file(path: str, nonce_hex: str) -> bytes:
    encrypted = Path(path).read_bytes()
    return decrypt_bytes(encrypted, nonce_hex)


def delete_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        return
