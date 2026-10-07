"""Private map normalization and conservative, non-executing repair previews.

Coordinates are native V1 millimetres, not screenshot pixels. IMAGE block
layout and cell flags follow vacuum-map-parser-roborock 0.1.5. The opaque raw
response is consumed only in memory and is never exposed or persisted here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from collections import Counter
from dataclasses import dataclass, replace
from itertools import groupby, pairwise
from typing import Any

from roborock_mcp.errors import DomainError, ErrorCode
from roborock_mcp.models import SplitLine
from roborock_mcp.security import opaque_key

CELL_MM = 50
MAX_CELLS = 4_000_000
OUTSIDE, WALL, UNASSIGNED, UNKNOWN = 0, -1, -2, -3


def _unsupported(message: str) -> DomainError:
    return DomainError(ErrorCode.UNSUPPORTED_CAPABILITY, message)


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    left: int
    bottom: int
    cells: tuple[int, ...]

    def point(self, index: int) -> tuple[int, int]:
        return (
            self.left + (index % self.width) * CELL_MM + CELL_MM // 2,
            self.bottom + (index // self.width) * CELL_MM + CELL_MM // 2,
        )

    def room_cells(self, segment: int) -> list[int]:
        return [i for i, value in enumerate(self.cells) if value == segment]

    def geometry(self) -> dict[str, Any]:
        runs = []
        offset = 0
        for label, values in groupby(self.cells):
            length = sum(1 for _ in values)
            runs.append([offset, length, label])
            offset += length
        return {
            "width": self.width,
            "height": self.height,
            "origin_mm": {"x": self.left, "y": self.bottom},
            "cell_size_mm": CELL_MM,
            "row_direction": "increasing_native_y",
            "encoding": "runs: [row_major_offset, length, segment_or_cell_kind]",
            "cell_kinds": {"outside": OUTSIDE, "wall": WALL, "unassigned": UNASSIGNED, "unknown": UNKNOWN},
            "runs": runs,
        }


def parse_grid(raw: bytes, removed: set[int] | None = None) -> Grid:
    """Extract a bounded V1 IMAGE block; reject missing or ambiguous geometry."""
    if len(raw) < 20 or len(raw) > 32_000_000 or raw[:2] != b"rr":
        raise _unsupported("Map does not contain a supported V1 grid.")
    start = struct.unpack_from("<H", raw, 2)[0]
    if start < 20 or start > len(raw):
        raise _unsupported("Invalid V1 map header.")
    if struct.unpack_from("<H", raw, 8)[0] != 1:
        raise _unsupported("Unsupported native map major version.")
    grid = None
    while start < len(raw):
        if start + 8 > len(raw):
            raise _unsupported("Truncated map block.")
        kind, header_size, data_size = struct.unpack_from("<HHI", raw, start)
        end = start + header_size + data_size
        if header_size < 8 or end > len(raw):
            raise _unsupported("Invalid map block size.")
        if kind == 2:
            if grid is not None or header_size < 24:
                raise _unsupported("Ambiguous map image block.")
            bottom, left, height, width = struct.unpack_from("<IIII", raw, start + header_size - 16)
            if not width or not height or width * height != data_size or data_size > MAX_CELLS:
                raise _unsupported("Invalid map grid dimensions.")
            if max(left + width, bottom + height) * CELL_MM > 100_000:
                raise _unsupported("Map extends beyond supported native coordinates.")
            pixels = raw[start + header_size : end]
            cells = []
            for index, value in enumerate(pixels):
                if index in (removed or ()) or value == 0:
                    label = OUTSIDE
                elif value in (255, 7):
                    label = UNASSIGNED
                elif value & 7 in (0, 1):
                    label = WALL
                elif value & 7 == 7:
                    label = value >> 3
                else:
                    label = UNKNOWN
                cells.append(label)
            grid = Grid(width, height, left * CELL_MM, bottom * CELL_MM, tuple(cells))
        start = end
    if grid is None:
        raise _unsupported("No map grid is available; no repair coordinates can be inferred.")
    return grid


def _coordinates(items: Any, fields: tuple[str, ...]) -> list[list[float]]:
    return sorted([[float(getattr(item, key)) for key in fields] for item in (items or [])])


@dataclass
class MapSnapshot:
    device: str
    map_id: str
    grid: Grid
    rooms: list[dict[str, Any]]
    restrictions: dict[str, Any]
    calibration: Any
    png: bytes | None
    binding_digest: str

    @property
    def revision(self) -> str:
        stable = {
            "device": self.device,
            "map": self.map_id,
            "geometry": self.grid.geometry(),
            "rooms": self.rooms,
            "bindings": self.binding_digest,
            "restrictions": self.restrictions,
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def result(self, format: str) -> dict[str, Any]:
        if format not in {"summary", "geometry", "image", "all", "repair_preview"}:
            raise DomainError(ErrorCode.INVALID_ARGUMENT, "Unknown map output format.")
        result: dict[str, Any] = {
            "format": format,
            "current_map": self.map_id,
            "map_revision": self.revision,
            "rooms": self.rooms,
            "recovery": {
                "backup": "unsupported_by_adapter",
                "restore": "unsupported_by_adapter",
                "backup_inventory": "inspect_in_official_app",
                "map_lock": "unverified",
                "snapshot_is_restorable_backup": False,
            },
        }
        if format in {"geometry", "all", "repair_preview"}:
            result["geometry"] = self.grid.geometry()
            result["restrictions"] = self.restrictions
            result["image_calibration"] = self.calibration
        if format in {"image", "all", "repair_preview"}:
            result["image"] = (
                {"mime_type": "image/png", "base64": base64.b64encode(self.png).decode()}
                if self.png
                else None
            )
        if format == "repair_preview":
            result["repair_preview"] = repair_preview(self)
        return result


def snapshot_from_props(props: Any, device: str, map_id: str) -> MapSnapshot:
    content = props.map_content
    data = content.map_data
    if data is None or not isinstance(content.raw_api_response, bytes):
        raise _unsupported("Native map content is unavailable.")
    grid = parse_grid(content.raw_api_response, data.additional_parameters.get("removed_map"))
    counts = Counter(label for label in grid.cells if label > 0)
    mappings = {int(room.segment_id): room for room in (props.rooms.rooms or [])}
    rooms = []
    for segment in sorted(counts):
        mapping = mappings.get(segment)
        rooms.append(
            {
                "key": opaque_key("room", f"{device}:{map_id}:{segment}"),
                "segment_id": segment,
                "name": mapping.name if mapping else None,
                "area_m2": counts[segment] * CELL_MM**2 / 1_000_000,
            }
        )
    bindings = sorted((int(r.segment_id), str(r.iot_id), r.name) for r in (props.rooms.rooms or []))
    digest = hashlib.sha256(json.dumps(bindings).encode()).hexdigest()
    return MapSnapshot(
        device,
        map_id,
        grid,
        rooms,
        {
            "virtual_walls": _coordinates(data.walls, ("x0", "y0", "x1", "y1")),
            **{
                name: _coordinates(getattr(data, name), ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"))
                for name in ("no_go_areas", "no_mopping_areas", "no_carpet_areas")
            },
        },
        data.calibration(),
        content.image_content,
        digest,
    )


def resolve_snapshot_rooms(snapshot: MapSnapshot, references: list[str]) -> list[int]:
    resolved = []
    for reference in references:
        matches = [
            r
            for r in snapshot.rooms
            if reference in (r["key"], str(r["segment_id"]))
            or (r["name"] and reference.casefold() == r["name"].casefold())
        ]
        if len(matches) != 1:
            raise DomainError(ErrorCode.AMBIGUOUS_REFERENCE, "Room reference is missing or ambiguous.")
        resolved.append(int(matches[0]["segment_id"]))
    if len(set(resolved)) != len(resolved):
        raise DomainError(ErrorCode.INVALID_ARGUMENT, "Select distinct rooms.")
    return resolved


def _components(grid: Grid, indices: list[int]) -> list[set[int]]:
    pending = set(indices)
    components = []
    while pending:
        queue = [pending.pop()]
        component = set(queue)
        while queue:
            index = queue.pop()
            x, y = index % grid.width, index // grid.width
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                neighbor = ny * grid.width + nx
                if 0 <= nx < grid.width and 0 <= ny < grid.height and neighbor in pending:
                    pending.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


def _connected(grid: Grid, indices: list[int]) -> bool:
    return len(_components(grid, indices)) == 1


def split_preview(snapshot: MapSnapshot, segment: int, line: SplitLine) -> dict[str, Any]:
    grid = snapshot.grid
    a, b = line.start, line.end
    dx, dy = b.x_mm - a.x_mm, b.y_mm - a.y_mm
    norm = dx * dx + dy * dy
    parts: list[list[int]] = [[], []]
    for index in grid.room_cells(segment):
        x, y = grid.point(index)
        projection = (x - a.x_mm) * dx + (y - a.y_mm) * dy
        if not 0 <= projection <= norm:
            raise DomainError(ErrorCode.INVALID_ARGUMENT, "Split endpoints must span the selected room.")
        side = dx * (y - a.y_mm) - dy * (x - a.x_mm)
        parts[int(side >= 0)].append(index)
    original_components = _components(grid, grid.room_cells(segment))
    part_sets = [set(part) for part in parts]
    if not all(len(part) >= 4 for part in parts) or any(
        not _connected(grid, list(intersection))
        for component in original_components
        for part in part_sets
        if (intersection := component & part)
    ):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "Split must produce two non-empty floor areas without fragmenting an existing component "
            "into disconnected pieces on the same side.",
        )
    return {
        "kind": "split_prediction",
        "part_cell_counts": [len(part) for part in parts],
        "part_area_m2": [len(part) * CELL_MM**2 / 1_000_000 for part in parts],
        "connectivity": {
            "original_component_count": len(original_components),
            "part_component_counts": [len(_components(grid, part)) for part in parts],
            "basis": "Each existing floor component remains connected on each side of the cut.",
            "existing_fragments_require_review": len(original_components) > 1,
        },
        "before_svg": grid_svg(grid),
        "after_svg": grid_svg(grid, highlighted=set(parts[1])),
        "warning": "Geometric prediction only; firmware determines new room IDs. Read back after dispatch.",
    }


def rooms_adjacent(grid: Grid, first: int, second: int) -> bool:
    return _indices_touch_room(grid, grid.room_cells(first), second)


def _indices_touch_room(grid: Grid, indices: list[int], second: int) -> bool:
    for index in indices:
        x, y = index % grid.width, index // grid.width
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < grid.width and 0 <= ny < grid.height and grid.cells[ny * grid.width + nx] == second:
                return True
    return False


def merge_preview(snapshot: MapSnapshot, rooms: list[int]) -> dict[str, Any]:
    if len(rooms) != 2 or not rooms_adjacent(snapshot.grid, *rooms):
        raise DomainError(ErrorCode.INVALID_ARGUMENT, "Merge requires two distinct adjacent rooms.")
    changed = tuple(rooms[0] if cell in rooms else cell for cell in snapshot.grid.cells)
    return {
        "kind": "merge_prediction",
        "affected_rooms": rooms,
        "before_svg": grid_svg(snapshot.grid),
        "after_svg": grid_svg(replace(snapshot.grid, cells=changed)),
        "warning": "Predicted union; firmware determines surviving room identity. Read back after dispatch.",
    }


def _doorway_gaps(positions: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for _, group in groupby(enumerate(sorted(positions)), lambda item: item[1] - item[0] * CELL_MM):
        values = [value for _, value in group]
        runs.append(values)
    return [
        [left[-1], right[0]]
        for left, right in pairwise(runs)
        if len(left) >= 8 and len(right) >= 8 and 150 <= right[0] - left[-1] - CELL_MM <= 1500
    ]


def grid_svg(grid: Grid, highlighted: set[int] | None = None) -> str:
    """Code-generated map diagram with native-y inversion; no external assets."""
    shapes = []
    for y in range(grid.height):
        row = [
            (-4 if highlighted and y * grid.width + x in highlighted else grid.cells[y * grid.width + x])
            for x in range(grid.width)
        ]
        x = 0
        for label, values in groupby(row):
            width = sum(1 for _ in values)
            if label != OUTSIDE:
                fill = (
                    "#ff3d7f"
                    if label == -4
                    else "#334155"
                    if label == WALL
                    else f"hsl({label * 137 % 360},65%,70%)"
                    if label > 0
                    else "#cbd5e1"
                )
                shapes.append(
                    f'<rect x="{x}" y="{grid.height - y - 1}" width="{width}" height="1" fill="{fill}"/>'
                )
            x += width
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {grid.width} {grid.height}" '
        f'shape-rendering="crispEdges">{"".join(shapes)}</svg>'
    )


def repair_preview(snapshot: MapSnapshot) -> dict[str, Any]:
    """Suggest cuts only where an observed wall axis separates room interiors.

    This is deliberately a candidate generator, not a SLAM geometry editor. It
    never treats a room's bounding box as its shape, or invents unseen walls.
    """
    grid = snapshot.grid
    candidates: list[dict[str, Any]] = []
    uncertain = ["One observation cannot establish historical wall positions or permanent drift prevention."]
    room_cells = {r["segment_id"]: grid.room_cells(r["segment_id"]) for r in snapshot.rooms}
    wall_axes: list[Counter[int]] = [Counter(), Counter()]
    wall_positions: list[dict[int, list[int]]] = [{}, {}]
    for index, label in enumerate(grid.cells):
        if label == WALL:
            x, y = grid.point(index)
            wall_axes[0][x] += 1
            wall_axes[1][y] += 1
            wall_positions[0].setdefault(x, []).append(y)
            wall_positions[1].setdefault(y, []).append(x)
    for axis in (0, 1):
        # Long observed wall runs only. A later read/user review establishes persistence.
        for coordinate, count in wall_axes[axis].most_common(24):
            if count < 20:
                continue
            gaps = _doorway_gaps(wall_positions[axis][coordinate])
            if not gaps:
                continue
            sides = {
                segment: [
                    sum(grid.point(i)[axis] < coordinate for i in indices),
                    sum(grid.point(i)[axis] >= coordinate for i in indices),
                ]
                for segment, indices in room_cells.items()
            }
            for segment, sizes in sides.items():
                total = sum(sizes)
                minority = min(sizes)
                if minority < 4 or minority / total > 0.10:
                    continue
                minority_side = int(sizes[1] < sizes[0])
                minority_indices = [
                    i for i in room_cells[segment] if int(grid.point(i)[axis] >= coordinate) == minority_side
                ]
                if not any(
                    low < grid.point(i)[1 - axis] < high for low, high in gaps for i in minority_indices
                ):
                    continue
                receivers = [
                    other
                    for other, parts in sides.items()
                    if other != segment
                    and parts[minority_side] >= 0.90 * sum(parts)
                    and _indices_touch_room(grid, minority_indices, other)
                ]
                if len(receivers) != 1:
                    continue
                if axis == 0:
                    start, end = (coordinate, grid.bottom), (coordinate, grid.bottom + grid.height * CELL_MM)
                else:
                    start, end = (grid.left, coordinate), (grid.left + grid.width * CELL_MM, coordinate)
                line = SplitLine.model_validate(
                    {"start": {"x_mm": start[0], "y_mm": start[1]}, "end": {"x_mm": end[0], "y_mm": end[1]}}
                )
                try:
                    prediction = split_preview(snapshot, segment, line)
                except DomainError:
                    continue
                candidates.append(
                    {
                        "room_segment": segment,
                        "possible_recipient": receivers[0],
                        "split_line": line.model_dump(),
                        "review_required": True,
                        "evidence": {
                            "observed_wall_cells_on_axis": count,
                            "minority_floor_cells": minority,
                            "possible_doorway_gaps_mm": gaps,
                        },
                        "steps": [
                            "Review the wall and doorway placement against the physical room.",
                            "Preview and approve one split; refresh map and resolve both new parts.",
                            "Merge only the confirmed misplaced part into the adjacent room.",
                        ],
                        "prediction": prediction,
                    }
                )
                if len(candidates) == 8:
                    break
            if len(candidates) == 8:
                break
        if len(candidates) == 8:
            break
    if not candidates:
        uncertain.append(
            "No sufficiently supported split was found; inspect a good backup or review manually."
        )
    return {
        "map_revision": snapshot.revision,
        "dispatches_writes": False,
        "diagnosis": "room_assignment_candidates" if candidates else "undetermined",
        "candidates": candidates,
        "uncertainties": uncertain,
        "before_svg": grid_svg(grid),
        "next_step": "Compare a known-good native backup in the official app before approving edits.",
    }
