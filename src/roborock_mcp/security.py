"""Logging redaction and opaque identifier helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_SECRET_KEYS = re.compile(
    r"(?:token|password|passwd|secret|local[_-]?key|rriot|pin|mqtt|turn|sdp|ice|credential)", re.IGNORECASE
)


def opaque_key(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()[:16]
    return f"{namespace}_{digest}"


def redact(value: Any) -> Any:
    """Recursively remove secrets and private binary payloads from diagnostics."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return f"[REDACTED BYTES: {len(value)}]"
    return value
