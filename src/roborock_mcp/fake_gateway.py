"""Deterministic in-memory gateway used by tests and dry runs."""

from __future__ import annotations

from typing import Any

from roborock_mcp.errors import DomainError, ErrorCode
from roborock_mcp.gateway import TOOL_CAPABILITIES, WRITE_TOOLS
from roborock_mcp.models import DeviceSummary, OperationResult, Verification


class FakeGateway:
    def __init__(self, *, stationary_repair: bool = False) -> None:
        self.stationary_repair = stationary_repair
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.device = DeviceSummary(key="device_test", name="Test S8 MaxV Ultra", model="roborock.vacuum.a97")
        self.state: dict[str, Any] = {
            "battery": 100,
            "robot_state": "charging",
            "map_revision": "fake-map-revision-1",
            "rooms": [{"key": "room_kitchen", "name": "Kitchen", "segment_id": 16}],
        }

    async def close(self) -> None:
        self.closed = True

    async def invoke(self, tool: str, arguments: dict[str, Any]) -> OperationResult:
        if self.stationary_repair and tool in WRITE_TOOLS and tool != "edit_rooms":
            raise DomainError(ErrorCode.CAPABILITY_DISABLED, "Stationary repair blocks this write.")
        self.calls.append((tool, arguments))
        result: dict[str, Any]
        if tool == "get_devices":
            result = {
                "devices": [
                    {
                        **self.device.model_dump(),
                        "protocol": "1.0",
                        "firmware": "fake",
                        "capabilities": {
                            name: {
                                "enabled": True,
                                "supported": True,
                                "source_backed": True,
                                "live_verified_on_a97": False,
                            }
                            for name in sorted(set(TOOL_CAPABILITIES.values()))
                        },
                    }
                ]
            }
        elif tool == "get_status":
            result = dict(self.state)
        elif tool == "get_map":
            result = {
                "map_revision": self.state["map_revision"],
                "rooms": self.state["rooms"],
                "format": arguments.get("format", "summary"),
            }
        elif tool == "remote_control":
            result = {
                "stopped": True,
                "moves_completed": len(arguments.get("moves") or []),
            }
        elif tool == "telepresence":
            result = {
                "action": arguments["action"],
                "session": arguments.get("session") or "media_test",
                "viewer_url": "http://127.0.0.1:1984/session/media_test",
            }
        else:
            result = {"tool": tool, "accepted": True, "arguments": arguments}
        return OperationResult(
            device=None if tool == "get_devices" else self.device,
            result=result,
            verification=Verification(
                state="unverified" if tool in WRITE_TOOLS else "verified",
                evidence={"fake_gateway": True, "live_verified_on_a97": False},
            ),
        )
