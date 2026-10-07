"""Logging redaction and opaque identifier helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_SECRET_KEYS = re.compile(
    r"(?:token|password|passwd|secret|local[_-]?key|rriot|mqtt|credential)", re.IGNORECASE
)
_PRIVATE_COMPONENTS = {"pin", "turn", "sdp", "ice"}


def _private_key(key: str) -> bool:
    # Short secrets are components, not substrings of mopping, returning or device.
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", words)
    components = set(re.split(r"[^a-z0-9]+", words.casefold()))
    return bool(_SECRET_KEYS.search(key) or components & _PRIVATE_COMPONENTS)


def opaque_key(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()[:16]
    return f"{namespace}_{digest}"


def redact(value: Any) -> Any:
    """Recursively remove secrets and private binary payloads from diagnostics."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _private_key(str(key)) else redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return f"[REDACTED BYTES: {len(value)}]"
    return value
