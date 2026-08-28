"""Safe conversion of upstream Roborock objects to JSON-compatible data."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from enum import Enum
from typing import Any

from roborock_mcp.security import redact


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return f"[BINARY:{len(value)}]"
    if hasattr(value, "model_dump"):
        return redact({key: to_jsonable(item) for key, item in value.model_dump().items()})
    if hasattr(value, "as_dict"):
        return redact({key: to_jsonable(item) for key, item in value.as_dict().items()})
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return redact({key: to_jsonable(item) for key, item in dataclasses.asdict(value).items()})
    if isinstance(value, dict):
        return redact({str(key): to_jsonable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return redact(
            {
                key: to_jsonable(item)
                for key, item in vars(value).items()
                if not key.startswith("_") and not callable(item)
            }
        )
    return str(value)
