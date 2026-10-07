"""Single-dispatch map writes for the pinned python-roborock adapter.

RpcChannel.send_command retries across transports. Select one strategy before
dispatch and call its single-attempt primitive instead. Never fall back after
publishing, including on timeout, rejection, cancellation, or malformed replies.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import version
from typing import Any

from roborock.devices.rpc.v1_channel import RpcChannel
from roborock.protocols.v1_protocol import RequestMessage

from roborock_mcp.errors import DomainError, ErrorCode, outcome_uncertain


class _QuietRpcLogger:
    """The upstream debug logger includes request parameters and raw responses."""

    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass


async def send_map_write_once(
    props: Any,
    command: str,
    params: Any,
    *,
    device: str,
    map_id: str,
    active_map_after_dispatch: bool = False,
) -> Any:
    arguments: dict[str, Any] = {"device": device, "format": "all"}
    if not active_map_after_dispatch:
        arguments["map"] = map_id
    try:
        await send_rpc_write_once(
            props.maps.rpc_channel, command, params, reconcile_tool="get_map", arguments=arguments
        )
    except DomainError as error:
        if active_map_after_dispatch and error.code == ErrorCode.OUTCOME_UNCERTAIN:
            error.reconcile_with.insert(0, {"tool": "get_status", "arguments": {"device": device}})
        raise
    return {"acknowledged": True, "read_back_required": True}


async def send_rpc_write_once(
    channel: Any,
    command: str,
    params: Any,
    *,
    reconcile_tool: str,
    arguments: dict[str, Any],
) -> Any:
    """Dispatch one RPC write; never enter the upstream transport fallback loop."""
    if version("python-roborock") != "7.1.1" or not isinstance(channel, RpcChannel):
        raise DomainError(ErrorCode.UNSUPPORTED_CAPABILITY, "Single-dispatch adapter is unavailable.")
    # This private integration is deliberately pinned and covered by contract tests.
    strategies = channel._rpc_strategies_cb()
    if not strategies:
        raise DomainError(ErrorCode.DEVICE_OFFLINE, "No write transport is available.")
    strategy = strategies[0]
    request = RequestMessage(command, params=params)
    try:
        response = await RpcChannel._send_rpc(strategy, request, _QuietRpcLogger())  # type: ignore[arg-type]
        if response not in ("ok", ["ok"]):
            raise ValueError("Unexpected acknowledgement")
    except (Exception, asyncio.CancelledError) as exc:
        raise outcome_uncertain(
            "Dispatch began; its outcome is uncertain. Do not repeat the write.",
            reconcile_tool,
            arguments,
        ) from exc
    return response
