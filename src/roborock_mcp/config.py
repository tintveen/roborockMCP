"""Non-secret profile configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, ConfigDict, Field

APP_NAME = "roborockmcp"


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    username_hint: str | None = None
    base_url: str | None = None
    default_device_key: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)
    media_sidecar_path: str | None = None


class ProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_path(APP_NAME) / "profiles.json"

    def load_all(self) -> dict[str, Profile]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {name: Profile.model_validate(data) for name, data in raw.items()}

    def get(self, name: str) -> Profile | None:
        return self.load_all().get(name)

    def put(self, profile: Profile) -> None:
        profiles = self.load_all()
        profiles[profile.name] = profile
        self._write(profiles)

    def delete(self, name: str) -> None:
        profiles = self.load_all()
        profiles.pop(name, None)
        self._write(profiles)

    def _write(self, profiles: dict[str, Profile]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            name: profile.model_dump(mode="json") for name, profile in profiles.items()
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


def data_directory() -> Path:
    path = user_data_path(APP_NAME)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def default_capabilities() -> dict[str, bool]:
    prefixes = (
        "read.",
        "control.",
        "write.",
        "privacy.",
    )
    names = (
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
        "control.patrol",
        "control.remote_control",
        "write.cleaning_settings",
        "write.dock_settings",
        "write.map_lifecycle",
        "write.map_rooms",
        "write.map_boundaries",
        "write.schedule",
        "write.maintenance",
        "write.device_settings",
        "privacy.obstacle_photos",
        "privacy.camera",
        "privacy.microphone",
    )
    return {name: True for name in names if name.startswith(prefixes)}
