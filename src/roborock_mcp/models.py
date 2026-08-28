"""Public typed models shared by the MCP tools and gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceSummary(StrictModel):
    key: str
    name: str
    model: str


class Verification(StrictModel):
    state: Literal["verified", "unverified", "not_applicable", "outcome_uncertain"]
    observed_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    evidence: dict[str, Any] = Field(default_factory=dict)


class OperationResult(StrictModel):
    ok: Literal[True] = True
    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    device: DeviceSummary | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    verification: Verification = Field(default_factory=lambda: Verification(state="not_applicable"))
    warnings: list[str] = Field(default_factory=list)


class Point(StrictModel):
    x_mm: int = Field(ge=0, le=100_000)
    y_mm: int = Field(ge=0, le=100_000)


class Zone(StrictModel):
    min_x_mm: int = Field(ge=0, le=100_000)
    min_y_mm: int = Field(ge=0, le=100_000)
    max_x_mm: int = Field(ge=0, le=100_000)
    max_y_mm: int = Field(ge=0, le=100_000)

    @model_validator(mode="after")
    def ordered(self) -> Zone:
        if self.min_x_mm >= self.max_x_mm or self.min_y_mm >= self.max_y_mm:
            raise ValueError("zone minimum coordinates must be lower than maximum coordinates")
        return self


class SplitLine(StrictModel):
    start: Point
    end: Point

    @model_validator(mode="after")
    def non_zero(self) -> SplitLine:
        if self.start == self.end:
            raise ValueError("split line must have a non-zero length")
        return self


class BoundaryGeometry(StrictModel):
    rectangle: Zone | None = None
    line: SplitLine | None = None
    polygon: list[Point] | None = Field(default=None, min_length=3, max_length=32)

    @model_validator(mode="after")
    def exactly_one_geometry(self) -> BoundaryGeometry:
        if sum(value is not None for value in (self.rectangle, self.line, self.polygon)) != 1:
            raise ValueError("exactly one of rectangle, line, or polygon is required")
        return self


class Motion(StrictModel):
    direction: Literal["forward", "backward", "turn_left", "turn_right"]
    speed: Literal["slow", "normal"] = "slow"
    duration_ms: int = Field(ge=100, le=1_000)


class CleaningSettings(StrictModel):
    vacuum_power: str | None = None
    mop_intensity: str | None = None
    route: str | None = None
    passes: int | None = Field(default=None, ge=1, le=3)
    carpet_behavior: str | None = None
    cleaning_sequence: list[str] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def at_least_one(self) -> CleaningSettings:
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("at least one cleaning setting is required")
        return self


class DockSettings(StrictModel):
    auto_empty: bool | None = None
    dust_collection_mode: str | None = None
    mop_wash_mode: str | None = None
    mop_wash_interval_min: int | None = Field(default=None, ge=10, le=50)
    water_temperature: str | None = None
    drying_duration_hours: int | None = Field(default=None, ge=2, le=4)
    detergent_auto_dose: bool | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> DockSettings:
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("at least one dock setting is required")
        return self


class DeviceSettings(StrictModel):
    dnd: dict[str, Any] | None = None
    led: bool | None = None
    child_lock: bool | None = None
    sound_volume: int | None = Field(default=None, ge=0, le=100)
    timezone: str | None = None
    obstacle_avoidance: bool | None = None
    furniture_recognition: bool | None = None
    floor_material_recognition: bool | None = None
    dirty_object_detection: bool | None = None
    camera_enabled: bool | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> DeviceSettings:
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("at least one device setting is required")
        return self


class ScheduleSpec(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    local_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(min_length=1, max_length=7)
    target: dict[str, Any]
    settings: CleaningSettings | None = None
