from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from roborock_mcp.fake_gateway import FakeGateway
from roborock_mcp.server import SERVER_INSTRUCTIONS, build_server

EXPECTED_TOOLS = [
    "get_devices",
    "get_status",
    "get_map",
    "get_cleaning_settings",
    "get_dock_status",
    "get_automations",
    "get_cleaning_history",
    "get_maintenance",
    "get_obstacles",
    "get_device_settings",
    "start_cleaning",
    "control_cleaning",
    "run_routine",
    "set_cleaning_settings",
    "dock_action",
    "set_dock_settings",
    "navigate",
    "remote_control",
    "manage_map",
    "edit_rooms",
    "edit_map_boundaries",
    "manage_schedule",
    "reset_consumable",
    "set_device_settings",
    "telepresence",
]


@asynccontextmanager
async def connected(fake_gateway: FakeGateway) -> AsyncIterator[Client]:
    async with Client(build_server(gateway=fake_gateway), raise_exceptions=True) as client:
        yield client


@pytest.mark.asyncio
async def test_exact_tool_contract_and_annotations(fake_gateway: FakeGateway) -> None:
    async with connected(fake_gateway) as client:
        listed = await client.list_tools()
    assert [tool.name for tool in listed.tools] == EXPECTED_TOOLS
    assert all(tool.annotations and tool.annotations.open_world_hint for tool in listed.tools)
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in listed.tools[:10])
    assert all(tool.annotations and not tool.annotations.read_only_hint for tool in listed.tools[10:])
    assert len(SERVER_INSTRUCTIONS[:512]) <= 512
    assert "OUTCOME_UNCERTAIN" in SERVER_INSTRUCTIONS
    snapshot = Path(__file__).parent / "snapshots" / "tool_schemas.json"
    assert [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in listed.tools
    ] == json.loads(snapshot.read_text())


VALID_CALLS = {
    "get_devices": {},
    "get_status": {},
    "get_map": {"format": "summary"},
    "get_cleaning_settings": {},
    "get_dock_status": {},
    "get_automations": {},
    "get_cleaning_history": {"limit": 5},
    "get_maintenance": {},
    "get_obstacles": {"include_photos": False},
    "get_device_settings": {},
    "start_cleaning": {"mode": "rooms", "rooms": ["Kitchen"], "passes": 1},
    "control_cleaning": {"action": "pause"},
    "run_routine": {"routine": "Morning"},
    "set_cleaning_settings": {"scope": "global", "settings": {"vacuum_power": "balanced"}},
    "dock_action": {"action": "wash_mop"},
    "set_dock_settings": {"settings": {"auto_empty": True}},
    "navigate": {"action": "goto", "point": {"x_mm": 1000, "y_mm": 2000}},
    "remote_control": {
        "action": "move",
        "moves": [{"direction": "forward", "speed": "slow", "duration_ms": 100}],
    },
    "manage_map": {"action": "rename", "map": "123", "name": "Home"},
    "edit_rooms": {
        "action": "rename",
        "rooms": ["Kitchen"],
        "map_revision": "rev1",
        "name": "Galley",
        "device": "device_test",
        "map": "0",
    },
    "edit_map_boundaries": {
        "action": "add",
        "kind": "no_go_zone",
        "map_revision": "rev1",
        "geometry": {"rectangle": {"min_x_mm": 1, "min_y_mm": 1, "max_x_mm": 100, "max_y_mm": 100}},
    },
    "manage_schedule": {
        "action": "create",
        "spec": {
            "name": "Test",
            "local_time": "23:59",
            "days": ["sun"],
            "target": {"rooms": ["Kitchen"]},
        },
    },
    "reset_consumable": {"consumable": "filter_work_time"},
    "set_device_settings": {"settings": {"led": True}},
    "telepresence": {"action": "open", "mode": "video", "ttl_seconds": 30},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), VALID_CALLS.items())
async def test_all_tools_return_structured_results(
    fake_gateway: FakeGateway, tool: str, arguments: dict[str, Any]
) -> None:
    async with connected(fake_gateway) as client:
        response = await client.call_tool(tool, arguments)
    assert response.is_error is False
    assert response.structured_content is not None
    assert response.structured_content["ok"] is True
    assert "operation_id" in response.structured_content


@pytest.mark.asyncio
async def test_cross_field_validation_is_visible_to_model(fake_gateway: FakeGateway) -> None:
    async with connected(fake_gateway) as client:
        response = await client.call_tool("navigate", {"action": "goto"})
    assert response.is_error is True
    assert isinstance(response.content[0], TextContent)
    assert "exactly one" in response.content[0].text


@pytest.mark.asyncio
async def test_model_validation_rejects_unbounded_remote_control(fake_gateway: FakeGateway) -> None:
    async with connected(fake_gateway) as client:
        response = await client.call_tool(
            "remote_control",
            {
                "action": "move",
                "moves": [{"direction": "forward", "speed": "normal", "duration_ms": 1000} for _ in range(6)],
            },
        )
    assert response.is_error is True
    assert isinstance(response.content[0], TextContent)
    assert "5000" in response.content[0].text


@pytest.mark.asyncio
async def test_gateway_not_owned_by_server_is_not_closed(fake_gateway: FakeGateway) -> None:
    async with Client(build_server(gateway=fake_gateway)):
        pass
    assert fake_gateway.closed is False
