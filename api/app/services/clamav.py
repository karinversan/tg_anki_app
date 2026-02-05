from __future__ import annotations

import socket

import pyclamd

from app.core.config import settings


def _clamd_client() -> pyclamd.ClamdNetworkSocket:
    return pyclamd.ClamdNetworkSocket(settings.clamav_host, settings.clamav_port)


def scan_bytes(data: bytes) -> None:
    try:
        client = _clamd_client()
        result = client.scan_stream(data)
    except (pyclamd.ConnectionError, socket.error) as exc:
        if settings.clamav_required:
            raise ValueError("ClamAV unavailable") from exc
        return

    if result:
        raise ValueError("Virus detected")
