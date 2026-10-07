from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from roborock_mcp.cli import app
from roborock_mcp.errors import DomainError, ErrorCode
from roborock_mcp.live_test import STAGES, render_plan
from roborock_mcp.media import MediaSupervisor, _free_loopback_port, discover_media_binary


def test_live_plan_is_non_executing_and_reset_exempt() -> None:
    text = render_plan()
    assert len(STAGES) == 9
    assert "nothing is executed" in text
    assert "Consumable reset" in text


def test_cli_version_and_live_guard() -> None:
    runner = CliRunner()
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == "0.1.0"
    blocked = runner.invoke(app, ["live-test", "run"])
    assert blocked.exit_code == 2
    assert "Refusing" in blocked.output


def test_media_discovery_and_loopback_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert discover_media_binary() is None
    binary = tmp_path / "sidecar"
    binary.write_text("fake")
    assert discover_media_binary(str(binary)) == binary
    assert 0 < _free_loopback_port() < 65536


def test_browser_url_is_loopback_and_carries_ephemeral_basic_auth(tmp_path: Path) -> None:
    supervisor = MediaSupervisor(tmp_path / "sidecar", port=19840)
    url = supervisor._url("/webrtc.html", {"src": "test"}, browser_auth=True)
    assert url.startswith("http://roborockmcp:")
    assert "@127.0.0.1:19840/webrtc.html?src=test" in url


@pytest.mark.asyncio
async def test_media_supervisor_rejects_missing_binary(tmp_path: Path) -> None:
    supervisor = MediaSupervisor(tmp_path / "missing")
    with pytest.raises(DomainError) as raised:
        await supervisor.start()
    assert raised.value.code == ErrorCode.MEDIA_UNAVAILABLE
