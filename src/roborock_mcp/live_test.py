"""Human-in-the-loop test manifest and guarded runner primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveTestStage:
    number: int
    name: str
    effects: str
    prerequisites: tuple[str, ...]


STAGES = (
    LiveTestStage(1, "doctor", "No robot changes", ("Run locally from the private checkout",)),
    LiveTestStage(2, "authenticate", "Creates a Roborock session in the OS keyring", ("User enters OTP",)),
    LiveTestStage(3, "read-only", "Reads household and map state", ("Codex approvals for map/images",)),
    LiveTestStage(
        4, "low-risk-writes", "Changes then restores DND, LED, volume, and cleaning", ("Human at robot",)
    ),
    LiveTestStage(
        5, "app-capture", "Temporarily proxies official-app traffic", ("Temporary CA", "Remove CA afterwards")
    ),
    LiveTestStage(
        6,
        "maps-automations",
        "Mutates then restores rooms, boundaries, and schedules",
        ("Safe test geometry",),
    ),
    LiveTestStage(
        7, "dock-navigation-rc", "Moves robot and operates dock", ("Clear floor", "Immediate stop access")
    ),
    LiveTestStage(
        8, "camera-voice", "Starts remote viewing and browser microphone", ("Homesec enabled", "Gesture PIN")
    ),
    LiveTestStage(
        9, "acceptance", "Writes only a sanitized compatibility report", ("All prior cleanup verified",)
    ),
)


def render_plan() -> str:
    lines = ["Human-in-the-loop stages (nothing is executed by this command):"]
    for stage in STAGES:
        requirements = "; ".join(stage.prerequisites)
        lines.append(f"{stage.number}. {stage.name}: {stage.effects}. Requires: {requirements}.")
    lines.append("Consumable reset is intentionally excluded from live testing.")
    return "\n".join(lines)
