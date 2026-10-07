"""MCP v2 server exposing the fixed 25-tool semantic contract."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from roborock_mcp.auth import CredentialStore
from roborock_mcp.config import ProfileStore
from roborock_mcp.errors import DomainError, ErrorCode
from roborock_mcp.gateway import WRITE_TOOLS, Gateway, RoborockGateway
from roborock_mcp.models import (
    BoundaryGeometry,
    CleaningSettings,
    DeviceSettings,
    DockSettings,
    Motion,
    OperationResult,
    Point,
    ScheduleSpec,
    SplitLine,
    Zone,
)
from roborock_mcp.version import __version__

SERVER_INSTRUCTIONS = (
    "Use get_devices and get_status before control. Resolve rooms and maps from fresh reads; never guess "
    "identifiers. Every spatial write must use the current map_revision. Codex must obtain approval for "
    "writes, maps, imagery, remote control, camera, and microphone. Never automatically retry a write "
    "reported as OUTCOME_UNCERTAIN; call the returned reconcile_with tool first. Live camera sessions are "
    "loopback-only, short-lived, and never recorded."
)


@dataclass(slots=True)
class AppContext:
    gateway: Gateway
    stationary_repair: bool = False


def _annotations(*, read_only: bool, destructive: bool = False, idempotent: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive if not read_only else None,
        idempotent_hint=idempotent if not read_only else None,
        open_world_hint=True,
    )


async def _invoke(ctx: Context[AppContext], tool: str, arguments: dict[str, Any]) -> OperationResult:
    try:
        context = ctx.request_context.lifespan_context
        if context.stationary_repair and tool in WRITE_TOOLS and tool != "edit_rooms":
            raise DomainError(ErrorCode.CAPABILITY_DISABLED, "Stationary repair blocks this write.")
        return await context.gateway.invoke(tool, arguments)
    except DomainError as exc:
        payload = {"ok": False, "error": exc.as_dict()}
        raise ToolError(json.dumps(payload, separators=(",", ":"))) from exc


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json", exclude_none=True) if hasattr(value, "model_dump") else value


def build_server(
    *, profile_name: str = "full-s8", gateway: Gateway | None = None, stationary_repair: bool = False
) -> MCPServer[AppContext]:
    owns_gateway = gateway is None

    @asynccontextmanager
    async def lifespan(server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        del server
        selected = gateway
        if selected is None:
            profile = ProfileStore().get(profile_name)
            if profile is None:
                raise RuntimeError(
                    f"Profile '{profile_name}' does not exist. Run "
                    f"'roborock-mcp auth login --profile {profile_name}'."
                )
            if stationary_repair:
                profile = profile.model_copy(update={"stationary_repair": True})
            selected = await RoborockGateway.connect(profile, CredentialStore())
        try:
            yield AppContext(selected, stationary_repair=stationary_repair)
        finally:
            if owns_gateway:
                await selected.close()

    mcp = MCPServer(
        "roborockMCP",
        title="Roborock MCP",
        description="Codex-first semantic control for Roborock V1 vacuums.",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
        log_level="WARNING",
    )

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_devices(ctx: Context[AppContext], device: str | None = None) -> OperationResult:
        """List devices and their enabled, source-backed, and live-verified capabilities."""
        return await _invoke(ctx, "get_devices", {"device": device})

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_status(ctx: Context[AppContext], device: str | None = None) -> OperationResult:
        """Read fresh battery, robot task, error, map, dock, progress, and position state."""
        return await _invoke(ctx, "get_status", {"device": device})

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_map(
        ctx: Context[AppContext],
        device: str | None = None,
        map: str | None = None,
        format: Literal["summary", "geometry", "image", "all", "repair_preview"] = "summary",
    ) -> Annotated[CallToolResult, OperationResult]:
        """Read sensitive home layout data. Codex must ask before image or geometry access."""
        result = await _invoke(ctx, "get_map", {"device": device, "map": map, "format": format})
        payload = result.model_dump(mode="json")
        rendered = payload["result"].get("image")
        content: list[Any] = []
        if rendered and rendered.get("base64"):
            content.append(ImageContent(type="image", data=rendered["base64"], mime_type="image/png"))
            payload["result"]["image"] = {"mime_type": "image/png", "content_index": 0}
        content.append(TextContent(type="text", text=json.dumps(payload)))
        return CallToolResult(content=content, structured_content=payload)

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_cleaning_settings(
        ctx: Context[AppContext], device: str | None = None, rooms: list[str] | None = None
    ) -> OperationResult:
        """Read global or room cleaning modes and the valid choices reported by the device."""
        return await _invoke(ctx, "get_cleaning_settings", {"device": device, "rooms": rooms})

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_dock_status(ctx: Context[AppContext], device: str | None = None) -> OperationResult:
        """Read dock operation, errors, supported actions, and current dock settings."""
        return await _invoke(ctx, "get_dock_status", {"device": device})

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_automations(ctx: Context[AppContext], device: str | None = None) -> OperationResult:
        """List native Roborock schedules and routines with opaque identifiers."""
        return await _invoke(ctx, "get_automations", {"device": device})

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_cleaning_history(
        ctx: Context[AppContext],
        device: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        cursor: str | None = None,
    ) -> OperationResult:
        """Read aggregate cleaning statistics and a bounded page of cleaning history."""
        return await _invoke(
            ctx,
            "get_cleaning_history",
            {
                "device": device,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "limit": limit,
                "cursor": cursor,
            },
        )

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_maintenance(ctx: Context[AppContext], device: str | None = None) -> OperationResult:
        """Read consumable work times, remaining estimates, and resettable opaque keys."""
        return await _invoke(ctx, "get_maintenance", {"device": device})

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_obstacles(
        ctx: Context[AppContext],
        device: str | None = None,
        map: str | None = None,
        include_photos: bool = False,
        obstacle_keys: Annotated[list[str] | None, Field(max_length=10)] = None,
    ) -> OperationResult:
        """Read obstacle metadata and optionally sensitive onboard photos. Codex must ask first."""
        return await _invoke(
            ctx,
            "get_obstacles",
            {
                "device": device,
                "map": map,
                "include_photos": include_photos,
                "obstacle_keys": obstacle_keys,
            },
        )

    @mcp.tool(annotations=_annotations(read_only=True), structured_output=True)
    async def get_device_settings(ctx: Context[AppContext], device: str | None = None) -> OperationResult:
        """Read DND, LED, child lock, sound, camera, and supported AI settings."""
        return await _invoke(ctx, "get_device_settings", {"device": device})

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def start_cleaning(
        ctx: Context[AppContext],
        mode: Literal["all", "rooms", "zones", "spot"],
        device: str | None = None,
        map: str | None = None,
        rooms: list[str] | None = None,
        zones: list[Zone] | None = None,
        point: Point | None = None,
        passes: Annotated[int, Field(ge=1, le=3)] = 1,
        settings: CleaningSettings | None = None,
    ) -> OperationResult:
        """Start one bounded full, room, zone, or spot cleaning task after Codex approval."""
        if mode == "rooms" and not rooms:
            raise ToolError("rooms mode requires at least one room")
        if mode == "zones" and not zones:
            raise ToolError("zones mode requires at least one zone")
        return await _invoke(
            ctx,
            "start_cleaning",
            {
                "device": device,
                "mode": mode,
                "map": map,
                "rooms": rooms,
                "zones": [_dump(zone) for zone in zones] if zones else None,
                "point": _dump(point),
                "passes": passes,
                "settings": _dump(settings),
            },
        )

    @mcp.tool(annotations=_annotations(read_only=False, idempotent=True), structured_output=True)
    async def control_cleaning(
        ctx: Context[AppContext],
        action: Literal["pause", "resume", "stop", "return_to_dock"],
        device: str | None = None,
    ) -> OperationResult:
        """Pause, resume, stop, or dock the current cleaning task."""
        return await _invoke(ctx, "control_cleaning", {"device": device, "action": action})

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def run_routine(
        ctx: Context[AppContext], routine: str, device: str | None = None
    ) -> OperationResult:
        """Execute exactly one routine selected from get_automations."""
        return await _invoke(ctx, "run_routine", {"device": device, "routine": routine})

    @mcp.tool(
        annotations=_annotations(read_only=False, destructive=True, idempotent=True), structured_output=True
    )
    async def set_cleaning_settings(
        ctx: Context[AppContext],
        scope: Literal["global", "rooms"],
        settings: CleaningSettings,
        device: str | None = None,
        rooms: list[str] | None = None,
    ) -> OperationResult:
        """Set supported cleaning modes globally or for explicit rooms, then read back state."""
        if scope == "rooms" and not rooms:
            raise ToolError("room scope requires at least one room")
        return await _invoke(
            ctx,
            "set_cleaning_settings",
            {"device": device, "scope": scope, "rooms": rooms, "settings": _dump(settings)},
        )

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def dock_action(
        ctx: Context[AppContext],
        action: Literal[
            "empty_dustbin",
            "stop_empty",
            "wash_mop",
            "stop_wash",
            "dry_mop",
            "stop_dry",
            "wash_then_charge",
            "empty_rinse_tank",
        ],
        device: str | None = None,
    ) -> OperationResult:
        """Run one capability-gated physical dock action."""
        return await _invoke(ctx, "dock_action", {"device": device, "action": action})

    @mcp.tool(
        annotations=_annotations(read_only=False, destructive=True, idempotent=True), structured_output=True
    )
    async def set_dock_settings(
        ctx: Context[AppContext], settings: DockSettings, device: str | None = None
    ) -> OperationResult:
        """Set supported dock options and reject values not advertised by the device."""
        return await _invoke(ctx, "set_dock_settings", {"device": device, "settings": _dump(settings)})

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def navigate(
        ctx: Context[AppContext],
        action: Literal["goto", "stop", "start_patrol", "start_pet_patrol", "resume_patrol", "stop_patrol"],
        device: str | None = None,
        map: str | None = None,
        room: str | None = None,
        point: Point | None = None,
    ) -> OperationResult:
        """Navigate to one fresh-map target or control a supported patrol mode."""
        if action == "goto" and (room is None) == (point is None):
            raise ToolError("goto requires exactly one of room or point")
        return await _invoke(
            ctx,
            "navigate",
            {
                "device": device,
                "action": action,
                "map": map,
                "target": {"room": room, "point": _dump(point)},
            },
        )

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def remote_control(
        ctx: Context[AppContext],
        action: Literal["move", "stop"],
        device: str | None = None,
        moves: Annotated[list[Motion] | None, Field(max_length=8)] = None,
    ) -> OperationResult:
        """Perform a dead-man bounded RC sequence. Codex must ask and a human must supervise."""
        if action == "move" and not moves:
            raise ToolError("move requires at least one bounded motion")
        if moves and sum(move.duration_ms for move in moves) > 5_000:
            raise ToolError("total motion may not exceed 5000 ms")
        return await _invoke(
            ctx,
            "remote_control",
            {"device": device, "action": action, "moves": [_dump(move) for move in moves] if moves else None},
        )

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def manage_map(
        ctx: Context[AppContext],
        action: Literal["start_quick_mapping", "resume_mapping", "switch", "rename"],
        device: str | None = None,
        map: str | None = None,
        name: Annotated[str | None, Field(max_length=40)] = None,
        expected_map_revision: str | None = None,
    ) -> OperationResult:
        """Quick-map, resume, switch, or rename; deletion, backup, and recovery are not exposed."""
        if action in {"switch", "rename"} and map is None:
            raise ToolError(f"{action} requires a map")
        if action == "rename" and not name:
            raise ToolError("rename requires a name")
        return await _invoke(
            ctx,
            "manage_map",
            {
                "device": device,
                "action": action,
                "map": map,
                "name": name,
                "expected_map_revision": expected_map_revision,
            },
        )

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def edit_rooms(
        ctx: Context[AppContext],
        action: Literal["rename", "split", "merge"],
        rooms: Annotated[list[str], Field(min_length=1, max_length=2)],
        map_revision: str,
        device: str,
        map: str,
        name: Annotated[str | None, Field(max_length=40)] = None,
        split_line: SplitLine | None = None,
        dry_run: bool = True,
    ) -> OperationResult:
        """Preview by default; explicitly apply one reviewed split/merge. Rename needs the official app."""
        if action in {"rename", "split"} and len(rooms) != 1:
            raise ToolError(f"{action} requires exactly one room")
        if action == "merge" and len(rooms) != 2:
            raise ToolError("merge requires exactly two rooms")
        if action == "rename" and not name:
            raise ToolError("rename requires a name")
        if action == "split" and split_line is None:
            raise ToolError("split requires a split_line")
        return await _invoke(
            ctx,
            "edit_rooms",
            {
                "device": device,
                "map": map,
                "map_revision": map_revision,
                "action": action,
                "rooms": rooms,
                "name": name,
                "split_line": _dump(split_line),
                "dry_run": dry_run,
            },
        )

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def edit_map_boundaries(
        ctx: Context[AppContext],
        action: Literal["add", "replace", "remove"],
        kind: Literal[
            "no_go_zone", "no_mop_zone", "virtual_wall", "carpet", "ignore_carpet_zone", "threshold"
        ],
        map_revision: str,
        device: str | None = None,
        map: str | None = None,
        boundary: str | None = None,
        geometry: BoundaryGeometry | None = None,
    ) -> OperationResult:
        """Add, replace, or remove one capability-gated map boundary on a current map revision."""
        if action in {"add", "replace"} and geometry is None:
            raise ToolError(f"{action} requires geometry")
        if action in {"replace", "remove"} and boundary is None:
            raise ToolError(f"{action} requires a boundary key")
        return await _invoke(
            ctx,
            "edit_map_boundaries",
            {
                "device": device,
                "map": map,
                "map_revision": map_revision,
                "action": action,
                "kind": kind,
                "boundary": boundary,
                "geometry": _dump(geometry),
            },
        )

    @mcp.tool(annotations=_annotations(read_only=False, destructive=True), structured_output=True)
    async def manage_schedule(
        ctx: Context[AppContext],
        action: Literal["create", "update", "delete", "enable", "disable"],
        device: str | None = None,
        schedule: str | None = None,
        spec: ScheduleSpec | None = None,
    ) -> OperationResult:
        """Create, update, delete, enable, or disable one native Roborock schedule."""
        if action in {"create", "update"} and spec is None:
            raise ToolError(f"{action} requires spec")
        if action != "create" and schedule is None:
            raise ToolError(f"{action} requires schedule")
        return await _invoke(
            ctx,
            "manage_schedule",
            {"device": device, "action": action, "schedule": schedule, "spec": _dump(spec)},
        )

    @mcp.tool(
        annotations=_annotations(read_only=False, destructive=True, idempotent=True), structured_output=True
    )
    async def reset_consumable(
        ctx: Context[AppContext], consumable: str, device: str | None = None
    ) -> OperationResult:
        """Irreversibly reset one reported consumable counter; intentionally exempt from a97 live testing."""
        return await _invoke(ctx, "reset_consumable", {"device": device, "consumable": consumable})

    @mcp.tool(
        annotations=_annotations(read_only=False, destructive=True, idempotent=True), structured_output=True
    )
    async def set_device_settings(
        ctx: Context[AppContext], settings: DeviceSettings, device: str | None = None
    ) -> OperationResult:
        """Set supported DND, LED, lock, sound, camera, and AI options, then reconcile state."""
        return await _invoke(ctx, "set_device_settings", {"device": device, "settings": _dump(settings)})

    @mcp.tool(annotations=_annotations(read_only=False), structured_output=True)
    async def telepresence(
        ctx: Context[AppContext],
        action: Literal["snapshot", "open", "close", "set_volume"],
        device: str | None = None,
        mode: Literal["video", "voice_chat"] = "video",
        quality: Literal["low", "standard", "high"] = "high",
        volume: Annotated[int | None, Field(ge=0, le=100)] = None,
        session: str | None = None,
        ttl_seconds: Annotated[int, Field(ge=30, le=300)] = 120,
    ) -> OperationResult:
        """Control a loopback-only camera/voice session. Codex must ask; no recording is supported."""
        if action in {"close", "set_volume"} and session is None:
            raise ToolError(f"{action} requires session")
        if action == "set_volume" and volume is None:
            raise ToolError("set_volume requires volume")
        return await _invoke(
            ctx,
            "telepresence",
            {
                "device": device,
                "action": action,
                "mode": mode,
                "quality": quality,
                "volume": volume,
                "session": session,
                "ttl_seconds": ttl_seconds,
            },
        )

    return mcp


def run_server(profile_name: str = "full-s8", *, stationary_repair: bool = False) -> None:
    build_server(profile_name=profile_name, stationary_repair=stationary_repair).run("stdio")
