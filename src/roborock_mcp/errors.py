"""Stable domain errors exposed to MCP clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    DEVICE_SELECTION_REQUIRED = "DEVICE_SELECTION_REQUIRED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    AMBIGUOUS_REFERENCE = "AMBIGUOUS_REFERENCE"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_STATE = "INVALID_STATE"
    STALE_MAP_REVISION = "STALE_MAP_REVISION"
    RATE_LIMITED = "RATE_LIMITED"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    MEDIA_UNAVAILABLE = "MEDIA_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(slots=True)
class DomainError(Exception):
    """Expected error safe to show to an MCP client."""

    code: ErrorCode
    message: str
    retryable: bool = False
    dispatch: str = "not_sent"
    reconcile_with: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "dispatch": self.dispatch,
            "reconcile_with": self.reconcile_with,
            "details": self.details,
        }


def outcome_uncertain(message: str, reconcile_tool: str, arguments: dict[str, Any]) -> DomainError:
    return DomainError(
        ErrorCode.OUTCOME_UNCERTAIN,
        message,
        retryable=False,
        dispatch="sent_or_unknown",
        reconcile_with=[{"tool": reconcile_tool, "arguments": arguments}],
    )
