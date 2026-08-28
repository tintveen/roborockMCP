from __future__ import annotations

import json
import stat
from pathlib import Path

from roborock_mcp.auth import _masked_email
from roborock_mcp.config import Profile, ProfileStore, default_capabilities


def test_profile_store_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    profile = Profile(name="full-s8", capabilities=default_capabilities())
    store.put(profile)
    assert store.get("full-s8") == profile
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["full-s8"]["name"] == "full-s8"
    store.delete("full-s8")
    assert store.get("full-s8") is None


def test_default_profile_enables_full_surface() -> None:
    capabilities = default_capabilities()
    assert capabilities["write.map_rooms"] is True
    assert capabilities["privacy.camera"] is True
    assert capabilities["control.remote_control"] is True


def test_email_masking() -> None:
    assert _masked_email("person@example.com") == "p***@example.com"
    assert _masked_email("not-an-email") == "***"
