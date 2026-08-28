"""Gateway contracts and the real python-roborock adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlparse

from roborock.devices.device import RoborockDevice
from roborock.devices.device_manager import DeviceManager, UserParams, create_device_manager
from roborock.exceptions import RoborockException
from roborock.web_api import RoborockApiClient

from roborock_mcp.auth import CredentialStore, StoredCredentials
from roborock_mcp.config import Profile
from roborock_mcp.errors import DomainError, ErrorCode, outcome_uncertain
from roborock_mcp.media import MediaSupervisor, discover_media_binary
from roborock_mcp.models import DeviceSummary, OperationResult, Verification
from roborock_mcp.security import opaque_key
from roborock_mcp.serialization import to_jsonable


class Gateway(Protocol):
    async def close(self) -> None: ...

    async def invoke(self, tool: str, arguments: dict[str, Any]) -> OperationResult: ...


@dataclass(slots=True)
class ResolvedDevice:
    device: RoborockDevice
    summary: DeviceSummary


SOURCE_BACKED = {
    "read.device",
    "read.status",
    "read.map",
    "read.settings",
    "read.dock",
    "read.automation",
    "read.history",
    "read.maintenance",
    "read.obstacles",
    "control.cleaning",
    "control.routines",
    "control.dock",
    "control.navigation",
    "control.remote_control",
    "write.cleaning_settings",
    "write.maintenance",
    "write.device_settings",
    "privacy.obstacle_photos",
}

TOOL_CAPABILITIES = {
    "get_devices": "read.device",
    "get_status": "read.status",
    "get_map": "read.map",
    "get_cleaning_settings": "read.settings",
    "get_dock_status": "read.dock",
    "get_automations": "read.automation",
    "get_cleaning_history": "read.history",
    "get_maintenance": "read.maintenance",
    "get_obstacles": "read.obstacles",
    "get_device_settings": "read.settings",
    "start_cleaning": "control.cleaning",
    "control_cleaning": "control.cleaning",
    "run_routine": "control.routines",
    "set_cleaning_settings": "write.cleaning_settings",
    "dock_action": "control.dock",
    "set_dock_settings": "write.dock_settings",
    "navigate": "control.navigation",
    "remote_control": "control.remote_control",
    "manage_map": "write.map_lifecycle",
    "edit_rooms": "write.map_rooms",
    "edit_map_boundaries": "write.map_boundaries",
    "manage_schedule": "write.schedule",
    "reset_consumable": "write.maintenance",
    "set_device_settings": "write.device_settings",
    "telepresence": "privacy.camera",
}

WRITE_TOOLS = set(TOOL_CAPABILITIES) - {
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
}


class RoborockGateway:
    """Semantic wrapper around one long-lived python-roborock DeviceManager."""

    def __init__(
        self,
        profile: Profile,
        credentials: StoredCredentials,
        manager: DeviceManager,
        media: MediaSupervisor | None = None,
    ) -> None:
        self.profile = profile
        self.credentials = credentials
        self.manager = manager
        self.api = RoborockApiClient(credentials.username, base_url=credentials.base_url)
        binary = discover_media_binary(profile.media_sidecar_path)
        self.media = media or (MediaSupervisor(binary) if binary else None)

    @classmethod
    async def connect(
        cls,
        profile: Profile,
        credential_store: CredentialStore | None = None,
    ) -> RoborockGateway:
        credentials = (credential_store or CredentialStore()).load(profile.name)
        manager = await create_device_manager(
            UserParams(
                username=credentials.username,
                user_data=credentials.user_data,
                base_url=credentials.base_url,
            )
        )
        return cls(profile, credentials, manager)

    async def close(self) -> None:
        if self.media is not None:
            await self.media.close()
        await self.manager.close()
        if self.api.session and not self.api.session.closed:
            await self.api.session.close()

    async def invoke(self, tool: str, arguments: dict[str, Any]) -> OperationResult:
        capability = TOOL_CAPABILITIES[tool]
        if not self.profile.capabilities.get(capability, False):
            raise DomainError(
                ErrorCode.CAPABILITY_DISABLED,
                f"Capability '{capability}' is disabled for profile '{self.profile.name}'.",
                details={"required_capability": capability},
            )
        if tool == "get_devices":
            return await self._get_devices(arguments)
        resolved = await self._resolve_device(arguments.get("device"))
        try:
            handler = getattr(self, f"_{tool}")
            result = await handler(resolved, arguments)
        except DomainError:
            raise
        except TimeoutError as exc:
            if tool in WRITE_TOOLS:
                reconcile = _reconcile_tool(tool)
                raise outcome_uncertain(
                    "The command may have applied. Do not retry automatically.",
                    reconcile,
                    {"device": resolved.summary.key},
                ) from exc
            raise DomainError(
                ErrorCode.TRANSPORT_ERROR, "The Roborock request timed out.", retryable=True
            ) from exc
        except RoborockException as exc:
            if tool in WRITE_TOOLS:
                raise outcome_uncertain(
                    "Roborock returned an error after dispatch may have begun. Reconcile before retrying.",
                    _reconcile_tool(tool),
                    {"device": resolved.summary.key},
                ) from exc
            raise DomainError(ErrorCode.UPSTREAM_ERROR, str(exc), retryable=False) from exc
        return OperationResult(
            device=resolved.summary,
            result=to_jsonable(result),
            verification=Verification(
                state="unverified" if tool in WRITE_TOOLS else "verified",
                evidence={"source_backed": capability in SOURCE_BACKED, "live_verified_on_a97": False},
            ),
        )

    async def _resolve_device(self, reference: str | None) -> ResolvedDevice:
        devices = await self.manager.get_devices()
        if not devices:
            raise DomainError(ErrorCode.DEVICE_NOT_FOUND, "No compatible Roborock vacuum was discovered.")
        matches: list[RoborockDevice]
        if reference:
            ref = reference.casefold()
            matches = [
                item
                for item in devices
                if reference == opaque_key("device", item.duid) or item.name.casefold() == ref
            ]
        elif self.profile.default_device_key:
            matches = [
                item for item in devices if opaque_key("device", item.duid) == self.profile.default_device_key
            ]
        elif len(devices) == 1:
            matches = devices
        else:
            raise DomainError(
                ErrorCode.DEVICE_SELECTION_REQUIRED,
                "Multiple devices are available; pass an opaque device key or unique exact name.",
            )
        if not matches:
            raise DomainError(ErrorCode.DEVICE_NOT_FOUND, f"No device matches '{reference}'.")
        if len(matches) > 1:
            raise DomainError(ErrorCode.AMBIGUOUS_REFERENCE, f"Device reference '{reference}' is ambiguous.")
        device = matches[0]
        return ResolvedDevice(device, self._summary(device))

    def _summary(self, device: RoborockDevice) -> DeviceSummary:
        return DeviceSummary(
            key=opaque_key("device", device.duid),
            name=device.name,
            model=device.product.model or "unknown",
        )

    async def _get_devices(self, arguments: dict[str, Any]) -> OperationResult:
        devices = await self.manager.get_devices()
        payload = []
        for device in devices:
            item = self._summary(device)
            payload.append(
                {
                    **item.model_dump(),
                    "protocol": device.device_info.pv,
                    "firmware": device.device_info.fv,
                    "online": device.is_connected,
                    "local_connected": device.is_local_connected,
                    "capabilities": {
                        name: {
                            "enabled": enabled,
                            "supported": device.v1_properties is not None,
                            "source_backed": name in SOURCE_BACKED,
                            "live_verified_on_a97": False,
                        }
                        for name, enabled in sorted(self.profile.capabilities.items())
                    },
                }
            )
        return OperationResult(result={"devices": payload}, verification=Verification(state="verified"))

    @staticmethod
    def _props(resolved: ResolvedDevice) -> Any:
        props = resolved.device.v1_properties
        if props is None:
            raise DomainError(ErrorCode.UNSUPPORTED_CAPABILITY, "This tool requires a V1 vacuum.")
        return props

    async def _get_status(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await props.status.refresh()
        return props.status

    async def _get_map(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await asyncio.gather(props.status.refresh(), props.maps.refresh(), props.rooms.refresh())
        await props.map_content.refresh()
        map_data = to_jsonable(props.map_content)
        revision = _map_revision(props.map_content)
        return {
            "format": arguments.get("format", "summary"),
            "map_revision": revision,
            "current_map": props.maps.current_map,
            "maps": props.maps,
            "rooms": props.rooms,
            "map": map_data,
        }

    async def _get_cleaning_settings(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await props.status.refresh()
        return {
            "fan_power": props.status.fan_power,
            "water_box_mode": props.status.water_box_mode,
            "mop_mode": props.status.mop_mode,
            "clean_area": props.status.clean_area,
            "requested_rooms": arguments.get("rooms") or [],
        }

    async def _get_dock_status(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await props.status.refresh()
        refreshes = [
            trait.refresh()
            for trait in (props.dust_collection_mode, props.wash_towel_mode, props.smart_wash_params)
            if trait is not None
        ]
        if refreshes:
            await asyncio.gather(*refreshes)
        return {
            "status": props.status,
            "dust_collection_mode": props.dust_collection_mode,
            "wash_towel_mode": props.wash_towel_mode,
            "smart_wash_params": props.smart_wash_params,
        }

    async def _get_automations(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        routines, schedules = await asyncio.gather(
            props.routines.get_routines(),
            self.api.get_schedules(self.credentials.user_data, resolved.device.duid),
        )
        return {"routines": routines, "schedules": schedules}

    async def _get_cleaning_history(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await props.clean_summary.refresh()
        return {"summary": props.clean_summary, "cursor": None, "requested": arguments}

    async def _get_maintenance(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await props.consumables.refresh()
        return props.consumables

    async def _get_obstacles(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await props.map_content.refresh()
        result: dict[str, Any] = {"map": props.map_content, "photos": []}
        if arguments.get("include_photos") and props.obstacle_photos is None:
            raise DomainError(
                ErrorCode.UNSUPPORTED_CAPABILITY, "Obstacle photos are not supported by this device."
            )
        if arguments.get("include_photos"):
            result["requested_photo_keys"] = arguments.get("obstacle_keys") or []
            result["warning"] = "Photo payload extraction requires the live a97 fixture captured during HITL."
        return result

    async def _get_device_settings(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        traits = [props.dnd, props.sound_volume, props.child_lock, props.led_status, props.flow_led_status]
        await asyncio.gather(*(trait.refresh() for trait in traits if trait is not None))
        return {
            "dnd": props.dnd,
            "sound_volume": props.sound_volume,
            "child_lock": props.child_lock,
            "led": props.led_status,
            "flow_led": props.flow_led_status,
            "camera_status": await props.command.send("get_camera_status"),
            "collision_avoidance": await props.command.send("get_collision_avoid_status"),
        }

    async def _start_cleaning(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        mode = arguments["mode"]
        if mode == "all":
            response = await props.command.send("app_start")
        elif mode == "rooms":
            segments = await self._resolve_rooms(props, arguments.get("rooms") or [])
            response = await props.command.send(
                "app_segment_clean", [{"segments": segments, "repeat": arguments.get("passes", 1)}]
            )
        elif mode == "zones":
            zones = [
                [
                    zone["min_x_mm"],
                    zone["min_y_mm"],
                    zone["max_x_mm"],
                    zone["max_y_mm"],
                    arguments.get("passes", 1),
                ]
                for zone in arguments.get("zones") or []
            ]
            response = await props.command.send("app_zoned_clean", zones)
        else:
            response = await props.command.send("app_spot")
        await props.status.refresh()
        return {"response": response, "status": props.status}

    async def _control_cleaning(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        command = {
            "pause": "app_pause",
            "resume": "app_start",
            "stop": "app_stop",
            "return_to_dock": "app_charge",
        }[arguments["action"]]
        props = self._props(resolved)
        response = await props.command.send(command)
        await props.status.refresh()
        return {"response": response, "status": props.status}

    async def _run_routine(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        routines = await props.routines.get_routines()
        routine = _resolve_named(routines, arguments["routine"], id_fields=("id",), name_fields=("name",))
        await props.routines.execute_routine(int(routine.id))
        return {"routine": to_jsonable(routine)}

    async def _set_cleaning_settings(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        settings = arguments["settings"]
        command_map = {
            "vacuum_power": "set_custom_mode",
            "mop_intensity": "set_water_box_custom_mode",
            "route": "set_mop_mode",
            "passes": "set_clean_repeat_times",
            "cleaning_sequence": "set_clean_sequence",
            "carpet_behavior": "set_carpet_mode",
        }
        responses = {}
        for key, value in settings.items():
            if value is not None:
                responses[key] = await props.command.send(command_map[key], [value])
        await props.status.refresh()
        return {"responses": responses, "status": props.status}

    async def _dock_action(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        action = arguments["action"]
        command_params: dict[str, tuple[str, Any]] = {
            "empty_dustbin": ("app_start_collect_dust", None),
            "stop_empty": ("app_stop_collect_dust", None),
            "wash_mop": ("app_start_wash", None),
            "stop_wash": ("app_stop_wash", None),
            "dry_mop": ("app_set_dryer_status", {"status": 1}),
            "stop_dry": ("app_set_dryer_status", {"status": 0}),
            "wash_then_charge": ("start_wash_then_charge", None),
            "empty_rinse_tank": ("app_empty_rinse_tank_water", None),
        }
        command, params = command_params[action]
        response = await props.command.send(command, params)
        return {"response": response, "action": action}

    async def _set_dock_settings(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        command_map: dict[str, tuple[str, Callable[[Any], Any]]] = {
            "auto_empty": ("set_dust_collection_switch_status", lambda value: {"status": int(value)}),
            "dust_collection_mode": ("set_dust_collection_mode", lambda value: {"mode": value}),
            "mop_wash_mode": ("set_wash_towel_mode", lambda value: {"wash_mode": value}),
            "mop_wash_interval_min": ("set_wash_towel_params", lambda value: {"interval": value}),
            "water_temperature": ("set_wash_water_temperature", lambda value: {"temperature": value}),
            "drying_duration_hours": ("app_set_dryer_setting", lambda value: {"drying_time": value}),
            "detergent_auto_dose": ("set_auto_delivery_cleaning_fluid", lambda value: {"status": int(value)}),
        }
        responses = {}
        for key, value in arguments["settings"].items():
            if value is not None:
                command, serializer = command_map[key]
                responses[key] = await props.command.send(command, serializer(value))
        return {"responses": responses}

    async def _navigate(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        action = arguments["action"]
        if action == "goto":
            point = arguments.get("target", {}).get("point")
            if point is None:
                raise DomainError(ErrorCode.INVALID_ARGUMENT, "goto requires target.point for v0.1.0.dev0")
            response = await props.command.send("app_goto_target", [point["x_mm"], point["y_mm"]])
        else:
            command = {
                "stop": "stop_goto_target",
                "start_patrol": "app_start_patrol",
                "start_pet_patrol": "app_start_pet_patrol",
                "resume_patrol": "app_resume_patrol",
                "stop_patrol": "app_stop",
            }[action]
            response = await props.command.send(command)
        return {"response": response, "action": action}

    async def _remote_control(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        if arguments["action"] == "stop":
            await props.command.send("app_rc_stop")
            await props.command.send("app_rc_end")
            return {"stopped": True}
        moves = arguments.get("moves") or []
        total = sum(int(item["duration_ms"]) for item in moves)
        if total > 5_000:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT, "Total remote-control motion may not exceed 5000 ms."
            )
        await props.command.send("app_rc_start")
        try:
            for seqnum, motion in enumerate(moves, start=1):
                velocity, omega = _motion_values(motion["direction"], motion["speed"])
                await props.command.send(
                    "app_rc_move",
                    [
                        {
                            "omega": round(omega, 1),
                            "velocity": velocity,
                            "duration": motion["duration_ms"],
                            "seqnum": seqnum,
                        }
                    ],
                )
                await asyncio.sleep(motion["duration_ms"] / 1000)
        finally:
            await props.command.send("app_rc_stop")
            await props.command.send("app_rc_end")
        return {"moves_completed": len(moves), "duration_ms": total}

    async def _manage_map(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        if arguments.get("expected_map_revision"):
            await self._assert_map_revision(props, arguments["expected_map_revision"])
        action = arguments["action"]
        if action == "switch":
            map_flag = int(arguments["map"])
            await props.maps.set_current_map(map_flag)
            return {"current_map": props.maps.current_map}
        command_params = {
            "start_quick_mapping": ("app_start_build_map", None),
            "resume_mapping": ("app_resume_build_map", None),
            "rename": ("name_multi_map", [int(arguments["map"]), arguments["name"]]),
        }
        command, params = command_params[action]
        return {"response": await props.command.send(command, params), "action": action}

    async def _edit_rooms(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await self._assert_map_revision(props, arguments["map_revision"])
        rooms = await self._resolve_rooms(props, arguments["rooms"])
        action = arguments["action"]
        if action == "rename":
            params: Any = [rooms[0], arguments["name"]]
            command = "name_segment"
        elif action == "merge":
            params = rooms
            command = "merge_segment"
        else:
            line = arguments["split_line"]
            params = [
                rooms[0],
                line["start"]["x_mm"],
                line["start"]["y_mm"],
                line["end"]["x_mm"],
                line["end"]["y_mm"],
            ]
            command = "split_segment"
        return {"response": await props.command.send(command, params), "action": action}

    async def _edit_map_boundaries(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        await self._assert_map_revision(props, arguments["map_revision"])
        payload = {
            "action": arguments["action"],
            "kind": arguments["kind"],
            "boundary": arguments.get("boundary"),
            "geometry": arguments.get("geometry"),
        }
        return {"response": await props.command.send("save_map", [payload]), "candidate_payload": payload}

    async def _manage_schedule(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        action = arguments["action"]
        command = {
            "create": "set_server_timer",
            "update": "upd_server_timer",
            "delete": "del_server_timer",
            "enable": "upd_server_timer",
            "disable": "upd_server_timer",
        }[action]
        payload = arguments.get("spec") or {"id": arguments.get("schedule"), "enabled": action == "enable"}
        return {"response": await props.command.send(command, [payload]), "candidate_payload": payload}

    async def _reset_consumable(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        from roborock.devices.traits.v1.consumeable import ConsumableAttribute

        props = self._props(resolved)
        try:
            attribute = ConsumableAttribute(arguments["consumable"])
        except ValueError as exc:
            raise DomainError(ErrorCode.INVALID_ARGUMENT, "Unknown consumable key.") from exc
        await props.consumables.reset_consumable(attribute)
        return {"consumable": attribute.value, "live_verification_exempt": True}

    async def _set_device_settings(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        props = self._props(resolved)
        command_map: dict[str, tuple[str, Callable[[Any], Any]]] = {
            "led": ("set_led_status", lambda value: [int(value)]),
            "child_lock": ("set_child_lock_status", lambda value: {"lock_status": int(value)}),
            "sound_volume": ("change_sound_volume", lambda value: [value]),
            "timezone": ("set_timezone", lambda value: [value]),
            "obstacle_avoidance": ("set_collision_avoid_status", lambda value: {"status": int(value)}),
            "furniture_recognition": ("set_identify_furniture_status", lambda value: {"status": int(value)}),
            "floor_material_recognition": (
                "set_identify_ground_material_status",
                lambda value: {"status": int(value)},
            ),
            "dirty_object_detection": (
                "set_dirty_object_detect_status",
                lambda value: {"status": int(value)},
            ),
            "camera_enabled": ("set_camera_status", lambda value: {"status": int(value)}),
        }
        responses: dict[str, Any] = {}
        for key, value in arguments["settings"].items():
            if value is None:
                continue
            if key == "dnd":
                params: Any
                enabled = bool(value.get("enabled", True))
                command = "set_dnd_timer" if enabled else "close_dnd_timer"
                params = (
                    [value["start_hour"], value["start_minute"], value["end_hour"], value["end_minute"]]
                    if enabled
                    else None
                )
            else:
                command, serializer = command_map[key]
                params = serializer(value)
            responses[key] = await props.command.send(command, params)
        return {"responses": responses}

    async def _telepresence(self, resolved: ResolvedDevice, arguments: dict[str, Any]) -> Any:
        if self.media is None:
            raise DomainError(
                ErrorCode.MEDIA_UNAVAILABLE,
                "The patched media sidecar is not installed. Run 'roborock-mcp media build' and install.",
            )
        action = arguments["action"]
        if action == "close":
            return {"closed": await self.media.close_session(arguments["session"])}
        if action == "set_volume":
            props = self._props(resolved)
            response = await props.command.send("set_voice_chat_volume", [arguments["volume"]])
            return {
                "session": arguments["session"],
                "volume": arguments["volume"],
                "response": response,
            }
        source_uri = self._media_source_uri(resolved)
        session = await self.media.open(
            source_uri,
            mode=arguments["mode"],
            quality=arguments["quality"],
            ttl_seconds=arguments["ttl_seconds"],
        )
        result = {
            "session": session.key,
            "viewer_url": session.viewer_url,
            "expires_in_seconds": arguments["ttl_seconds"],
            "loopback_only": True,
            "recording": False,
        }
        if action == "snapshot":
            result["snapshot_url"] = session.viewer_url.replace("/webrtc.html", "/api/frame.jpeg").split(
                "&media=", 1
            )[0]
        return result

    def _media_source_uri(self, resolved: ResolvedDevice) -> str:
        rriot = self.credentials.user_data.rriot
        if rriot is None or rriot.r is None or not rriot.r.m:
            raise DomainError(ErrorCode.MEDIA_UNAVAILABLE, "Roborock MQTT media credentials are missing.")
        if not self.credentials.camera_pin:
            raise DomainError(
                ErrorCode.AUTH_REQUIRED,
                "A Homesec PIN is required. Run 'roborock-mcp auth set-camera-pin'.",
            )
        mqtt = rriot.r.m
        host = urlparse(mqtt if "://" in mqtt else f"mqtt://{mqtt}").netloc
        if not host:
            raise DomainError(ErrorCode.MEDIA_UNAVAILABLE, "Roborock MQTT media endpoint is invalid.")
        query = urlencode(
            {
                "u": rriot.u,
                "s": rriot.s,
                "k": rriot.k,
                "did": resolved.device.duid,
                "key": resolved.device.device_info.local_key,
                "pin": self.credentials.camera_pin,
            },
            quote_via=quote,
        )
        return f"roborock://{host}?{query}"

    @staticmethod
    async def _assert_map_revision(props: Any, expected: str) -> None:
        await props.map_content.refresh()
        current = _map_revision(props.map_content)
        if current != expected:
            raise DomainError(
                ErrorCode.STALE_MAP_REVISION,
                "The map changed after it was read. Fetch get_map and re-plan before writing.",
                details={"expected": expected, "current": current},
            )

    async def _resolve_rooms(self, props: Any, references: list[str]) -> list[int]:
        await props.rooms.refresh()
        rooms = props.rooms.rooms or []
        result: list[int] = []
        for reference in references:
            matches = [
                room
                for room in rooms
                if str(room.segment_id) == reference
                or opaque_key("room", str(room.segment_id)) == reference
                or (room.name and room.name.casefold() == reference.casefold())
            ]
            if len(matches) != 1:
                code = ErrorCode.AMBIGUOUS_REFERENCE if matches else ErrorCode.INVALID_ARGUMENT
                raise DomainError(code, f"Room reference '{reference}' did not resolve uniquely.")
            result.append(int(matches[0].segment_id))
        return result


def _motion_values(direction: str, speed: str) -> tuple[float, float]:
    velocity = 0.12 if speed == "slow" else 0.2
    angular = math.pi / 3 if speed == "slow" else math.pi / 2
    return {
        "forward": (velocity, 0.0),
        "backward": (-velocity, 0.0),
        "turn_left": (0.0, angular),
        "turn_right": (0.0, -angular),
    }[direction]


def _map_revision(map_content: Any) -> str:
    payload = json.dumps(to_jsonable(map_content), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _resolve_named(
    values: list[Any], reference: str, *, id_fields: tuple[str, ...], name_fields: tuple[str, ...]
) -> Any:
    matches = []
    for item in values:
        ids = [str(getattr(item, field, "")) for field in id_fields]
        names = [str(getattr(item, field, "")).casefold() for field in name_fields]
        if reference in ids or reference.casefold() in names:
            matches.append(item)
    if len(matches) != 1:
        code = ErrorCode.AMBIGUOUS_REFERENCE if matches else ErrorCode.INVALID_ARGUMENT
        raise DomainError(code, f"Reference '{reference}' did not resolve uniquely.")
    return matches[0]


def _reconcile_tool(tool: str) -> str:
    if tool in {"manage_map", "edit_rooms", "edit_map_boundaries"}:
        return "get_map"
    if tool in {"set_cleaning_settings", "set_device_settings"}:
        return "get_device_settings"
    if tool in {"dock_action", "set_dock_settings"}:
        return "get_dock_status"
    if tool == "manage_schedule":
        return "get_automations"
    if tool == "reset_consumable":
        return "get_maintenance"
    return "get_status"


@asynccontextmanager
async def connected_gateway(profile: Profile) -> AsyncIterator[RoborockGateway]:
    gateway = await RoborockGateway.connect(profile)
    try:
        yield gateway
    finally:
        await gateway.close()
