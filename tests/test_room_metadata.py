from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from map_fixtures import fixture_gateway

from roborock_mcp.errors import DomainError, ErrorCode
from roborock_mcp.maps import snapshot_from_props


def metadata_fixture(action: str) -> tuple[Any, Any, dict[str, Any]]:
    gateway, resolved, props = fixture_gateway()
    props.command = SimpleNamespace(send=AsyncMock(return_value=[]))
    props.rooms._refresh_rooms = AsyncMock(
        return_value=[
            SimpleNamespace(name="Office", iot_id="synthetic-office"),
            SimpleNamespace(name="Hall", iot_id="synthetic-hall"),
        ]
    )
    return (
        gateway,
        props,
        {
            "action": action,
            "device": resolved.summary.key,
            "map": "0",
            "map_revision": snapshot_from_props(props, resolved.summary.key, "0").revision,
            "rooms": ["17", "16"],
            "names": ["Office", "Hall"],
        },
    )


@pytest.mark.asyncio
async def test_name_preview_is_private_and_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _, arguments = metadata_fixture("restore_names")
    write = AsyncMock()
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", write)
    result = await gateway.invoke("edit_rooms", arguments)
    assert result.result["prediction"]["room_type_ids_after"] == 0
    assert "synthetic-office" not in str(result)
    write.assert_not_called()


@pytest.mark.asyncio
async def test_restore_existing_names_sends_complete_sorted_table(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, props, arguments = metadata_fixture("restore_names")
    write = AsyncMock(return_value={"acknowledged": True})
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", write)
    await gateway.invoke("edit_rooms", {**arguments, "dry_run": False, "reset_room_types": True})
    write.assert_awaited_once_with(
        props,
        "name_segment",
        [
            {"miRoomId": "synthetic-hall", "robotRoomId": 16},
            {"miRoomId": "synthetic-office", "robotRoomId": 17},
        ],
        device="device_synthetic",
        map_id="0",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing", "ambiguous", "partial", "duplicate_room", "duplicate_name", "type_consent"]
)
async def test_unsafe_name_restoration_fails_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    gateway, props, arguments = metadata_fixture("restore_names")
    arguments.update(dry_run=False, reset_room_types=True)
    if case == "missing":
        arguments["names"] = ["New cloud name", "Hall"]
    elif case == "ambiguous":
        props.rooms._refresh_rooms.return_value.append(
            SimpleNamespace(name="Office", iot_id="other-home-office")
        )
    elif case == "partial":
        arguments["rooms"] = ["16"]
    elif case == "duplicate_room":
        arguments["rooms"] = ["16", "16"]
    elif case == "duplicate_name":
        arguments["names"] = ["Office", "Office"]
    else:
        arguments["reset_room_types"] = False
    write = AsyncMock()
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", write)
    with pytest.raises(DomainError):
        await gateway.invoke("edit_rooms", arguments)
    write.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_binding_race_prevents_name_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, props, arguments = metadata_fixture("restore_names")
    first = props.rooms._refresh_rooms.return_value
    second = [SimpleNamespace(name="Office", iot_id="changed-office"), first[1]]
    props.rooms._refresh_rooms.side_effect = [first, second]
    write = AsyncMock()
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", write)
    with pytest.raises(DomainError) as error:
        await gateway.invoke("edit_rooms", {**arguments, "dry_run": False, "reset_room_types": True})
    assert error.value.code == ErrorCode.STALE_MAP_REVISION
    write.assert_not_called()


@pytest.mark.asyncio
async def test_sequence_preview_then_single_apply_and_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _, arguments = metadata_fixture("set_sequence")
    write = AsyncMock(return_value=["ok"])
    monkeypatch.setattr(gateway, "_write", write)
    preview = await gateway.invoke("edit_rooms", arguments)
    assert preview.result["prediction"]["previous_sequence"] == []
    write.assert_not_called()
    result = await gateway.invoke("edit_rooms", {**arguments, "dry_run": False, "expected_sequence": []})
    assert write.call_args.args[1:] == ("set_cleaning_settings", "set_clean_sequence", [17, 16])
    write.assert_awaited_once()
    assert result.result["reconcile_with"]["tool"] == "get_cleaning_settings"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_expected", "changed_order", "partial"])
async def test_order_requires_full_membership_and_fresh_order(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    gateway, props, arguments = metadata_fixture("set_sequence")
    arguments.update(dry_run=False)
    if case == "changed_order":
        arguments["expected_sequence"] = []
        props.command.send.side_effect = [[], [16, 17]]
    elif case == "partial":
        arguments.update(rooms=["16"], expected_sequence=[])
    write = AsyncMock()
    monkeypatch.setattr(gateway, "_write", write)
    with pytest.raises(DomainError):
        await gateway.invoke("edit_rooms", arguments)
    write.assert_not_called()
