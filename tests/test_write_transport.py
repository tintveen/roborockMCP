from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from map_fixtures import fixture_gateway
from roborock.devices.rpc.v1_channel import RpcChannel

from roborock_mcp.errors import DomainError, ErrorCode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "reconcile"),
    [
        ("start_cleaning", {"mode": "all"}, "get_status"),
        ("control_cleaning", {"action": "stop"}, "get_status"),
        ("set_cleaning_settings", {"settings": {"vacuum_power": 102, "passes": 2}}, "get_cleaning_settings"),
        ("dock_action", {"action": "wash_mop"}, "get_dock_status"),
        ("set_dock_settings", {"settings": {"auto_empty": True}}, "get_dock_status"),
        ("navigate", {"action": "stop"}, "get_status"),
        ("remote_control", {"action": "move", "moves": []}, "get_status"),
        ("manage_schedule", {"action": "delete", "schedule": "synthetic"}, "get_automations"),
        ("reset_consumable", {"consumable": "filter_work_time"}, "get_maintenance"),
        ("set_device_settings", {"settings": {"led": True, "sound_volume": 10}}, "get_device_settings"),
        ("telepresence", {"action": "set_volume", "volume": 10}, "get_status"),
    ],
)
@pytest.mark.parametrize("failure", [TimeoutError(), asyncio.CancelledError(), ValueError("private details")])
async def test_rpc_write_handlers_never_retry_or_continue_after_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    reconcile: str,
    failure: BaseException,
) -> None:
    gateway, resolved, props = fixture_gateway(stationary=False)
    channel = RpcChannel(lambda: cast(Any, ["local", "cloud-fallback"]), Mock())
    props.command = SimpleNamespace(_rpc_channel=channel, send=AsyncMock())
    gateway.media = Mock()
    dispatch = AsyncMock(side_effect=failure)
    monkeypatch.setattr(RpcChannel, "_send_rpc", dispatch)

    with pytest.raises(DomainError) as caught:
        await gateway.invoke(tool, {"device": resolved.summary.key, **arguments})

    dispatch.assert_awaited_once()
    assert dispatch.call_args.args[0] == "local"
    props.command.send.assert_not_called()
    assert caught.value.code == ErrorCode.OUTCOME_UNCERTAIN
    assert not caught.value.retryable
    assert caught.value.reconcile_with == [{"tool": reconcile, "arguments": {"device": resolved.summary.key}}]
    assert "private details" not in str(caught.value.as_dict())


@pytest.mark.asyncio
async def test_rc_move_failure_only_sends_distinct_safety_stop_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, resolved, props = fixture_gateway(stationary=False)
    props.command = SimpleNamespace(_rpc_channel=RpcChannel(lambda: cast(Any, ["local", "fallback"]), Mock()))
    dispatch = AsyncMock(side_effect=[["ok"], TimeoutError(), ["ok"], ["ok"]])
    monkeypatch.setattr(RpcChannel, "_send_rpc", dispatch)
    with pytest.raises(DomainError) as caught:
        await gateway.invoke(
            "remote_control",
            {
                "device": resolved.summary.key,
                "action": "move",
                "moves": [
                    {"direction": "forward", "speed": "slow", "duration_ms": 100},
                    {"direction": "backward", "speed": "slow", "duration_ms": 100},
                ],
            },
        )
    assert caught.value.code == ErrorCode.OUTCOME_UNCERTAIN
    assert [call.args[1].method for call in dispatch.call_args_list] == [
        "app_rc_start",
        "app_rc_move",
        "app_rc_stop",
        "app_rc_end",
    ]
    assert all(call.args[0] == "local" for call in dispatch.call_args_list)
