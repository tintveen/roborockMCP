"""Synthetic native map packets; no household data or hardware connections."""

from __future__ import annotations

import struct
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from roborock.devices.traits.v1.map_content import MapContentConverter
from roborock.map.map_parser import MapParser, MapParserConfig

from roborock_mcp.config import Profile, default_capabilities
from roborock_mcp.gateway import ResolvedDevice, RoborockGateway
from roborock_mcp.maps import Grid
from roborock_mcp.models import DeviceSummary


def synthetic_grid() -> Grid:
    width = height = 32
    cells = []
    for y in range(height):
        for x in range(width):
            if x in (0, width - 1) or y in (0, height - 1) or (x == 15 and not 13 <= y <= 16):
                value = -1
            else:
                value = 16 if x < 15 or (13 <= y <= 16 and x < 19) else 17
            cells.append(value)
    return Grid(width, height, 20_000, 20_000, tuple(cells))


def native_packet(grid: Grid) -> bytes:
    header = bytearray(20)
    header[:2] = b"rr"
    struct.pack_into("<H", header, 2, 20)
    struct.pack_into("<H", header, 8, 1)
    image = struct.pack(
        "<HHIIIII", 2, 24, len(grid.cells), grid.bottom // 50, grid.left // 50, grid.height, grid.width
    )
    pixels = bytes((v << 3 | 7) if v > 0 else {0: 0, -1: 1, -2: 255, -3: 2}[v] for v in grid.cells)
    return bytes(header) + image + pixels


def fixture_props() -> Any:
    content = MapContentConverter(MapParser(MapParserConfig())).convert(native_packet(synthetic_grid()))
    content.refresh = AsyncMock()  # type: ignore[attr-defined]
    return SimpleNamespace(
        status=SimpleNamespace(state=8, in_cleaning=0, refresh=AsyncMock()),
        maps=SimpleNamespace(current_map=0, rpc_channel=None),
        rooms=SimpleNamespace(
            rooms=[
                SimpleNamespace(segment_id=16, name="Office", iot_id="synthetic-office"),
                SimpleNamespace(segment_id=17, name="Hall", iot_id="synthetic-hall"),
            ],
            refresh=AsyncMock(),
        ),
        map_content=content,
    )


def fixture_gateway(*, stationary: bool = True) -> tuple[RoborockGateway, ResolvedDevice, Any]:
    props = fixture_props()
    device: Any = SimpleNamespace(v1_properties=props)
    resolved = ResolvedDevice(device, DeviceSummary(key="device_synthetic", name="Synthetic", model="fake"))
    gateway = object.__new__(RoborockGateway)
    gateway.profile = Profile(name="test", capabilities=default_capabilities(), stationary_repair=stationary)
    gateway._map_locks = {}
    gateway._resolve_device = AsyncMock(return_value=resolved)  # type: ignore[method-assign]
    return gateway, resolved, props
