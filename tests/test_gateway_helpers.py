from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from roborock_mcp.errors import DomainError, ErrorCode, outcome_uncertain
from roborock_mcp.gateway import (
    RoborockGateway,
    _map_revision,
    _motion_values,
    _reconcile_tool,
    _resolve_named,
)


@dataclass
class Named:
    id: int
    name: str


def test_motion_values_are_bounded() -> None:
    assert _motion_values("forward", "slow") == (0.12, 0.0)
    assert _motion_values("backward", "normal") == (-0.2, 0.0)
    assert _motion_values("turn_left", "slow")[1] > 0
    assert _motion_values("turn_right", "normal")[1] < 0


def test_named_resolution_and_errors() -> None:
    values = [Named(1, "Morning"), Named(2, "Evening")]
    assert _resolve_named(values, "1", id_fields=("id",), name_fields=("name",)).name == "Morning"
    assert _resolve_named(values, "evening", id_fields=("id",), name_fields=("name",)).id == 2
    with pytest.raises(DomainError) as missing:
        _resolve_named(values, "missing", id_fields=("id",), name_fields=("name",))
    assert missing.value.code == ErrorCode.INVALID_ARGUMENT


def test_reconciliation_mapping_and_error_payload() -> None:
    assert _reconcile_tool("manage_map") == "get_map"
    assert _reconcile_tool("dock_action") == "get_dock_status"
    assert _reconcile_tool("reset_consumable") == "get_maintenance"
    assert _reconcile_tool("start_cleaning") == "get_status"
    error = outcome_uncertain("maybe", "get_status", {"device": "x"})
    assert error.as_dict()["dispatch"] == "sent_or_unknown"
    assert error.as_dict()["retryable"] is False


class MapContent:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        self._refreshes = 0

    async def refresh(self) -> None:
        self._refreshes += 1


class Properties:
    def __init__(self, value: dict[str, Any]) -> None:
        self.map_content = MapContent(value)


def test_map_revision_is_deterministic() -> None:
    assert _map_revision({"b": 2, "a": 1}) == _map_revision({"a": 1, "b": 2})
    assert _map_revision({"a": 1}) != _map_revision({"a": 2})


@pytest.mark.asyncio
async def test_stale_map_revision_is_rejected_before_write() -> None:
    props = Properties({"rooms": [1, 2]})
    current = _map_revision(props.map_content)
    await RoborockGateway._assert_map_revision(props, current)
    assert props.map_content._refreshes == 1
    with pytest.raises(DomainError) as raised:
        await RoborockGateway._assert_map_revision(props, "stale")
    assert raised.value.code == ErrorCode.STALE_MAP_REVISION
    assert raised.value.details["current"] == current
