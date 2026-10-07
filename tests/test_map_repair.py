from __future__ import annotations

import asyncio
import base64
import struct
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from map_fixtures import fixture_gateway, fixture_props, native_packet, synthetic_grid
from mcp import Client
from mcp.types import ImageContent
from roborock.devices.rpc.v1_channel import RpcChannel

from roborock_mcp.errors import DomainError, ErrorCode
from roborock_mcp.fake_gateway import FakeGateway
from roborock_mcp.gateway import WRITE_TOOLS
from roborock_mcp.map_transport import send_map_write_once
from roborock_mcp.maps import (
    merge_preview,
    parse_grid,
    repair_preview,
    resolve_snapshot_rooms,
    snapshot_from_props,
    split_preview,
)
from roborock_mcp.models import SplitLine
from roborock_mcp.security import opaque_key
from roborock_mcp.server import build_server


def split_line() -> dict[str, Any]:
    return {"start": {"x_mm": 20_775, "y_mm": 20_000}, "end": {"x_mm": 20_775, "y_mm": 21_600}}


def mapping_fixture() -> tuple[Any, Any, Any, dict[str, Any]]:
    gateway, resolved, props = fixture_gateway(stationary=False)
    props.status.battery = 100
    props.maps.refresh = AsyncMock()
    props.maps.max_multi_map = 4
    props.maps.multi_map_count = 1
    props.maps.map_info = [SimpleNamespace(map_flag=0, name="Existing synthetic map")]
    snapshot = snapshot_from_props(props, resolved.summary.key, "0")
    arguments = {
        "device": resolved.summary.key,
        "map": "0",
        "action": "start_quick_mapping",
        "expected_map_revision": snapshot.revision,
    }
    return gateway, resolved, props, arguments


@pytest.mark.asyncio
async def test_quick_mapping_preview_never_dispatches_and_apply_sends_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, _, props, arguments = mapping_fixture()
    send = AsyncMock(return_value={"acknowledged": True})
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", send)
    preview = await gateway.invoke("manage_map", arguments)
    assert preview.result["dry_run"] is True
    assert preview.result["effects"]["robot_moves"] is True
    assert preview.result["effects"]["cleaning_requested"] is False
    send.assert_not_called()
    await gateway.invoke("manage_map", {**arguments, "dry_run": False})
    send.assert_awaited_once_with(
        props,
        "app_start_build_map",
        [],
        device="device_synthetic",
        map_id="0",
        active_map_after_dispatch=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["full", "unknown", "wrong_map", "missing_map", "undocked", "low_battery"]
)
async def test_quick_mapping_requires_free_slot_and_dock(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    gateway, _, props, arguments = mapping_fixture()
    send = AsyncMock()
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", send)
    if invalid == "full":
        props.maps.max_multi_map = 1
    elif invalid == "unknown":
        props.maps.max_multi_map = None
    elif invalid == "wrong_map":
        props.maps.map_info[0].map_flag = 1
    elif invalid == "missing_map":
        props.maps.map_info = []
    elif invalid == "undocked":
        props.status.state = 3
    else:
        props.status.battery = 19
    with pytest.raises(DomainError) as error:
        await gateway.invoke("manage_map", {**arguments, "dry_run": False})
    assert error.value.code == ErrorCode.INVALID_STATE
    send.assert_not_called()


@pytest.mark.asyncio
async def test_quick_mapping_inventory_race_prevents_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _, props, arguments = mapping_fixture()
    send = AsyncMock()
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", send)
    calls = 0

    async def refresh() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            props.maps.multi_map_count = 2
            props.maps.map_info.append(SimpleNamespace(map_flag=1, name="New concurrent map"))

    props.maps.refresh = refresh
    with pytest.raises(DomainError) as error:
        await gateway.invoke("manage_map", {**arguments, "dry_run": False})
    assert error.value.code == ErrorCode.STALE_MAP_REVISION
    send.assert_not_called()


@pytest.mark.asyncio
async def test_uncertain_mapping_start_reconciles_status_and_new_active_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = RpcChannel(lambda: cast(Any, ["first", "fallback"]), Mock())
    props = SimpleNamespace(maps=SimpleNamespace(rpc_channel=channel))
    send = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(RpcChannel, "_send_rpc", send)
    with pytest.raises(DomainError) as error:
        await send_map_write_once(
            props, "app_start_build_map", [], device="device_fake", map_id="0", active_map_after_dispatch=True
        )
    send.assert_awaited_once()
    assert error.value.code == ErrorCode.OUTCOME_UNCERTAIN
    assert error.value.reconcile_with == [
        {"tool": "get_status", "arguments": {"device": "device_fake"}},
        {"tool": "get_map", "arguments": {"device": "device_fake", "format": "all"}},
    ]


def test_native_geometry_and_render_calibration() -> None:
    props = fixture_props()
    snapshot = snapshot_from_props(props, "device_synthetic", "0")
    assert snapshot.grid == synthetic_grid()
    assert snapshot.grid.point(0) == (20_025, 20_025)
    assert base64.b64decode(snapshot.result("image")["image"]["base64"]).startswith(b"\x89PNG")
    for point in snapshot.calibration:
        # Upstream rendering has scale 4 and flips native y, without guessed screenshot offsets.
        assert point["map"]["x"] == (point["vacuum"]["x"] / 50 - 400) * 4
        assert point["map"]["y"] == (31 - (point["vacuum"]["y"] / 50 - 400)) * 4
    assert "image" not in snapshot.result("summary")
    assert "geometry" not in snapshot.result("summary")
    assert "image" not in snapshot.result("geometry")
    assert "raw_api_response" not in str(snapshot.result("all"))


def test_revisions_bind_device_map_grid_rooms_and_restrictions() -> None:
    props = fixture_props()
    before = snapshot_from_props(props, "device_synthetic", "0")
    props.map_content.image_content = b"different rendering"
    props.map_content.map_data.vacuum_position = SimpleNamespace(x=1, y=2)
    props.map_content.map_data.additional_parameters["map_sequence"] = 999
    assert snapshot_from_props(props, "device_synthetic", "0").revision == before.revision
    assert snapshot_from_props(props, "another-device", "0").revision != before.revision
    assert snapshot_from_props(props, "device_synthetic", "1").revision != before.revision
    props.rooms.rooms[0].iot_id = "new-binding"
    assert snapshot_from_props(props, "device_synthetic", "0").revision != before.revision
    assert replace(before, rooms=[{**before.rooms[0], "name": "Changed"}]).revision != before.revision
    assert replace(before, restrictions={"virtual_walls": [[1, 2, 3, 4]]}).revision != before.revision
    changed = list(before.grid.cells)
    changed[33] = 17  # same packet length, changed room membership
    assert replace(before, grid=replace(before.grid, cells=tuple(changed))).revision != before.revision


@pytest.mark.parametrize("damage", ["truncated", "zero-block", "dimensions", "duplicate", "no-image"])
def test_bad_native_grids_fail_closed(damage: str) -> None:
    raw = bytearray(native_packet(synthetic_grid()))
    if damage == "truncated":
        raw.pop()
    elif damage == "zero-block":
        struct.pack_into("<H", raw, 22, 0)
    elif damage == "dimensions":
        struct.pack_into("<I", raw, 40, 9000)
    elif damage == "duplicate":
        raw.extend(raw[20:])
    else:
        raw = raw[:20]
    with pytest.raises(DomainError):
        parse_grid(bytes(raw))


def test_removed_cells_are_not_floor() -> None:
    grid = parse_grid(native_packet(synthetic_grid()), {33})
    assert grid.cells[33] == 0


def test_merge_preview_changes_only_the_selected_membership() -> None:
    snapshot = snapshot_from_props(fixture_props(), "device_synthetic", "0")
    preview = merge_preview(snapshot, [16, 17])
    assert preview["before_svg"] != preview["after_svg"]
    with pytest.raises(DomainError):
        merge_preview(snapshot, [16, 23])


@pytest.mark.asyncio
async def test_unsupported_backup_and_rename_are_not_dispatched() -> None:
    gateway, resolved, props = fixture_gateway(stationary=False)
    snapshot = snapshot_from_props(props, resolved.summary.key, "0")
    async with Client(build_server(gateway=gateway), raise_exceptions=False) as client:
        result = await client.call_tool("manage_map", {"action": "backup", "device": resolved.summary.key})
        assert result.is_error
        result = await client.call_tool(
            "edit_rooms",
            {
                "device": resolved.summary.key,
                "map": "0",
                "map_revision": snapshot.revision,
                "action": "rename",
                "name": "New room",
                "rooms": ["16"],
                "dry_run": False,
            },
        )
        assert result.is_error
        assert "UNSUPPORTED_CAPABILITY" in str(result.content)
    assert props.maps.rpc_channel is None


@pytest.mark.asyncio
async def test_preflight_timeout_is_not_reported_as_a_dispatched_write() -> None:
    gateway, resolved, props = fixture_gateway()
    props.status.refresh.side_effect = TimeoutError()
    with pytest.raises(DomainError) as error:
        await gateway.invoke("edit_rooms", {"device": resolved.summary.key, "map": "0"})
    assert error.value.code == ErrorCode.TRANSPORT_ERROR
    assert error.value.dispatch == "not_sent"


def test_conservative_room_bleed_preview_and_no_invented_walls() -> None:
    snapshot = snapshot_from_props(fixture_props(), "device_synthetic", "0")
    preview = repair_preview(snapshot)
    assert preview["dispatches_writes"] is False
    assert any(c["room_segment"] == 16 and c["possible_recipient"] == 17 for c in preview["candidates"])
    assert all(c["review_required"] for c in preview["candidates"])
    empty_walls = replace(snapshot.grid, cells=tuple(-2 if v == -1 else v for v in snapshot.grid.cells))
    assert repair_preview(replace(snapshot, grid=empty_walls))["candidates"] == []
    assert "unverified" == snapshot.result("summary")["recovery"]["map_lock"]


def test_split_prediction_uses_membership_and_rejects_outside_or_short_cuts() -> None:
    snapshot = snapshot_from_props(fixture_props(), "device_synthetic", "0")
    prediction = split_preview(snapshot, 16, SplitLine.model_validate(split_line()))
    assert sum(prediction["part_cell_counts"]) == len(snapshot.grid.room_cells(16))
    assert prediction["before_svg"] != prediction["after_svg"]
    for x, end in ((19_000, 21_600), (20_775, 20_500)):
        line = SplitLine.model_validate(
            {"start": {"x_mm": x, "y_mm": 20_000}, "end": {"x_mm": x, "y_mm": end}}
        )
        with pytest.raises(DomainError):
            split_preview(snapshot, 16, line)


def test_room_keys_are_scoped_and_names_must_be_unique() -> None:
    snapshot = snapshot_from_props(fixture_props(), "device_synthetic", "0")
    foreign = snapshot_from_props(fixture_props(), "another-device", "0")
    assert resolve_snapshot_rooms(snapshot, [snapshot.rooms[0]["key"]]) == [16]
    for refs in ([foreign.rooms[0]["key"]], ["Office", "16"]):
        with pytest.raises(DomainError):
            resolve_snapshot_rooms(snapshot, refs)
    snapshot.rooms[1]["name"] = "Office"
    with pytest.raises(DomainError):
        resolve_snapshot_rooms(snapshot, ["Office"])


@pytest.mark.asyncio
async def test_duplicate_robot_names_require_opaque_identity() -> None:
    gateway, _, _ = fixture_gateway()
    del gateway._resolve_device  # use the real selector
    devices = [
        SimpleNamespace(duid=key, name="Same name", product=SimpleNamespace(model="fake"))
        for key in ("synthetic-one", "synthetic-two")
    ]
    gateway.manager = cast(Any, SimpleNamespace(get_devices=AsyncMock(return_value=devices)))
    for reference in (None, "Same name"):
        with pytest.raises(DomainError):
            await gateway._resolve_device(reference)
    selected = await gateway._resolve_device(opaque_key("device", "synthetic-two"))
    assert cast(Any, selected.device) is devices[1]


@pytest.mark.asyncio
async def test_wrong_map_read_never_switches_or_fetches_geometry() -> None:
    gateway, resolved, props = fixture_gateway()
    with pytest.raises(DomainError) as error:
        await gateway._get_map(resolved, {"map": "9"})
    assert error.value.code == ErrorCode.INVALID_ARGUMENT
    props.map_content.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_map_switch_during_read_is_rejected() -> None:
    gateway, resolved, props = fixture_gateway()
    props.map_content.refresh.side_effect = lambda: setattr(props.maps, "current_map", 1)
    with pytest.raises(DomainError) as error:
        await gateway._get_map(resolved, {})
    assert error.value.code == ErrorCode.STALE_MAP_REVISION


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS - {"edit_rooms"}))
async def test_stationary_mode_blocks_every_other_write_before_device_resolution(tool: str) -> None:
    gateway, _, _ = fixture_gateway()
    with pytest.raises(DomainError) as error:
        await gateway.invoke(tool, {"action": "start_quick_mapping"})
    assert error.value.code == ErrorCode.CAPABILITY_DISABLED
    gateway._resolve_device.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_edit_preview_and_apply_have_fresh_identity_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, resolved, props = fixture_gateway()
    send = AsyncMock(return_value={"acknowledged": True})
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", send)
    snapshot = snapshot_from_props(props, resolved.summary.key, "0")
    args = {
        "device": resolved.summary.key,
        "map": "0",
        "map_revision": snapshot.revision,
        "action": "split",
        "rooms": ["16"],
        "split_line": split_line(),
    }
    preview = await gateway.invoke("edit_rooms", args)
    assert preview.result["dry_run"] is True
    send.assert_not_called()
    for override in ({"device": "Synthetic"}, {"map": "2"}, {"map_revision": "old"}):
        with pytest.raises(DomainError):
            await gateway.invoke("edit_rooms", {**args, **override, "dry_run": False})
    props.status.state = 5
    with pytest.raises(DomainError):
        await gateway.invoke("edit_rooms", {**args, "dry_run": False})
    props.status.state = 8
    props.status.in_cleaning = 1
    with pytest.raises(DomainError):
        await gateway.invoke("edit_rooms", {**args, "dry_run": False})
    props.status.in_cleaning = 0
    send.assert_not_called()
    await gateway.invoke("edit_rooms", {**args, "dry_run": False})
    send.assert_awaited_once()
    assert send.call_args.args[1:3] == ("split_segment", [16, 20_775, 20_000, 20_775, 21_600])


@pytest.mark.asyncio
async def test_new_room_binding_invalidates_old_proposal() -> None:
    gateway, resolved, props = fixture_gateway()
    snapshot = snapshot_from_props(props, resolved.summary.key, "0")
    props.rooms.rooms[0].iot_id = "replacement-room"
    with pytest.raises(DomainError) as error:
        await gateway._map_edit_snapshot(
            resolved, {"device": resolved.summary.key, "map": "0", "map_revision": snapshot.revision}
        )
    assert error.value.code == ErrorCode.STALE_MAP_REVISION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError(), ValueError("private upstream details"), asyncio.CancelledError()]
)
async def test_single_dispatch_never_uses_fallback(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    channel = RpcChannel(lambda: cast(Any, ["first", "fallback"]), Mock())
    props = SimpleNamespace(maps=SimpleNamespace(rpc_channel=channel))
    send = AsyncMock(side_effect=failure)
    monkeypatch.setattr(RpcChannel, "_send_rpc", send)
    with pytest.raises(DomainError) as error:
        await send_map_write_once(props, "split_segment", [16, 1, 2, 3, 4], device="device_fake", map_id="0")
    send.assert_awaited_once()
    assert send.call_args.args[0] == "first"
    assert error.value.code == ErrorCode.OUTCOME_UNCERTAIN
    assert error.value.retryable is False
    assert error.value.reconcile_with[0]["arguments"]["map"] == "0"
    assert "private upstream details" not in str(error.value.as_dict())


@pytest.mark.asyncio
async def test_unexpected_write_acknowledgement_requires_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = RpcChannel(lambda: cast(Any, ["first"]), Mock())
    monkeypatch.setattr(RpcChannel, "_send_rpc", AsyncMock(return_value={"error": "bad parameters"}))
    with pytest.raises(DomainError) as error:
        await send_map_write_once(
            SimpleNamespace(maps=SimpleNamespace(rpc_channel=channel)),
            "merge_segment",
            [16, 17],
            device="device_fake",
            map_id="0",
        )
    assert error.value.code == ErrorCode.OUTCOME_UNCERTAIN


@pytest.mark.asyncio
async def test_mcp_returns_native_image_content() -> None:
    gateway, _, _ = fixture_gateway()
    async with Client(build_server(gateway=gateway)) as client:
        response = await client.call_tool("get_map", {"device": "device_synthetic", "format": "all"})
    assert response.is_error is False
    assert isinstance(response.content[0], ImageContent)
    assert response.structured_content is not None
    assert response.structured_content["result"]["image"]["content_index"] == 0


@pytest.mark.asyncio
async def test_server_stationary_mode_also_guards_injected_gateway() -> None:
    fake = FakeGateway()
    async with Client(build_server(gateway=fake, stationary_repair=True), raise_exceptions=False) as client:
        result = await client.call_tool("manage_map", {"action": "start_quick_mapping"})
    assert result.is_error
    assert fake.calls == []


@pytest.mark.asyncio
async def test_map_changes_on_last_preflight_prevent_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, resolved, props = fixture_gateway()
    snapshot = snapshot_from_props(props, resolved.summary.key, "0")
    count = 0

    def change_on_second_read() -> None:
        nonlocal count
        count += 1
        if count == 2:
            props.rooms.rooms[0].name = "Changed in another app"

    props.map_content.refresh.side_effect = change_on_second_read
    send = AsyncMock()
    monkeypatch.setattr("roborock_mcp.gateway.send_map_write_once", send)
    with pytest.raises(DomainError) as error:
        await gateway.invoke(
            "edit_rooms",
            {
                "device": resolved.summary.key,
                "map": "0",
                "map_revision": snapshot.revision,
                "action": "split",
                "rooms": ["16"],
                "split_line": split_line(),
                "dry_run": False,
            },
        )
    assert error.value.code == ErrorCode.STALE_MAP_REVISION
    send.assert_not_called()
