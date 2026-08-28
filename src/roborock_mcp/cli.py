"""roborock-mcp command-line interface."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from roborock_mcp.auth import CredentialStore, complete_login, request_login_code
from roborock_mcp.config import ProfileStore, data_directory
from roborock_mcp.fake_gateway import FakeGateway
from roborock_mcp.live_test import render_plan
from roborock_mcp.media import discover_media_binary
from roborock_mcp.server import build_server, run_server
from roborock_mcp.version import __version__

app = typer.Typer(help="Private Codex-first MCP server for Roborock vacuums.", no_args_is_help=True)
auth_app = typer.Typer(help="Manage out-of-band Roborock OTP credentials.")
profile_app = typer.Typer(help="Inspect non-secret local profiles.")
media_app = typer.Typer(help="Build and install the loopback media sidecar.")
live_app = typer.Typer(help="Plan guarded human-in-the-loop hardware validation.")
app.add_typer(auth_app, name="auth")
app.add_typer(profile_app, name="profile")
app.add_typer(media_app, name="media")
app.add_typer(live_app, name="live-test")


@app.callback(invoke_without_command=True)
def root(
    version: Annotated[
        bool, typer.Option("--version", help="Show the package version.", is_eager=True)
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def serve(
    profile: Annotated[str, typer.Option(help="Fixed profile loaded for the server lifespan.")] = "full-s8",
    fake: Annotated[
        bool, typer.Option(help="Use deterministic fake state; never contacts Roborock.")
    ] = False,
) -> None:
    """Run the MCP server over protocol-clean stdio."""
    if fake:
        build_server(profile_name=profile, gateway=FakeGateway()).run("stdio")
    else:
        run_server(profile)


@auth_app.command("login")
def auth_login(
    profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8",
    email: Annotated[str | None, typer.Option(help="Roborock account email.")] = None,
) -> None:
    """Request an email OTP and save the resulting session in the OS keyring."""
    username = email or typer.prompt("Roborock account email")

    async def login() -> None:
        client = await request_login_code(username)
        code = typer.prompt("Email verification code", hide_input=True)
        await complete_login(client, username, code, profile, CredentialStore(), ProfileStore())

    asyncio.run(login())
    typer.echo(f"Profile '{profile}' authenticated. No password was stored.")


@auth_app.command("set-camera-pin")
def auth_camera_pin(
    profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8",
) -> None:
    """Store the Homesec gesture PIN in the OS keyring."""
    pin = typer.prompt("Homesec gesture PIN", hide_input=True, confirmation_prompt=True)
    CredentialStore().set_camera_pin(profile, pin)
    typer.echo("Camera PIN stored in the OS keyring.")


@auth_app.command("status")
def auth_status(profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8") -> None:
    profiles = ProfileStore()
    stored = profiles.get(profile)
    payload = {
        "profile": profile,
        "configured": stored is not None,
        "authenticated": CredentialStore().has_login(profile),
        "username_hint": stored.username_hint if stored else None,
        "default_device_key": stored.default_device_key if stored else None,
    }
    typer.echo(json.dumps(payload, indent=2))


@auth_app.command("logout")
def auth_logout(profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8") -> None:
    CredentialStore().delete(profile)
    ProfileStore().delete(profile)
    typer.echo(f"Removed keyring credentials and local metadata for '{profile}'.")


@profile_app.command("show")
def profile_show(profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8") -> None:
    stored = ProfileStore().get(profile)
    if stored is None:
        typer.echo(f"Profile '{profile}' does not exist.", err=True)
        raise typer.Exit(1)
    typer.echo(stored.model_dump_json(indent=2))


@profile_app.command("set-device")
def profile_set_device(
    device_key: Annotated[str, typer.Argument(help="Opaque key returned by get_devices.")],
    profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8",
) -> None:
    store = ProfileStore()
    stored = store.get(profile)
    if stored is None:
        typer.echo(f"Profile '{profile}' does not exist.", err=True)
        raise typer.Exit(1)
    stored.default_device_key = device_key
    store.put(stored)
    typer.echo(f"Default device set for '{profile}'.")


@media_app.command("build")
def media_build() -> None:
    """Build the pinned, patched go2rtc sidecar from source."""
    root_dir = Path(__file__).resolve().parents[2]
    script = root_dir / "third_party" / "go2rtc" / "build.sh"
    subprocess.run([str(script)], check=True, cwd=root_dir)  # noqa: S603


@media_app.command("install")
def media_install(
    profile: Annotated[str, typer.Option(help="Profile receiving the sidecar path.")] = "full-s8",
) -> None:
    """Install an already-built sidecar in the private user data directory."""
    root_dir = Path(__file__).resolve().parents[2]
    source = root_dir / "build" / "media" / "go2rtc-roborockmcp"
    if not source.is_file():
        typer.echo("Sidecar is not built. Run 'roborock-mcp media build' first.", err=True)
        raise typer.Exit(1)
    destination = data_directory() / "bin" / "go2rtc-roborockmcp"
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o700)
    store = ProfileStore()
    stored = store.get(profile)
    if stored is not None:
        stored.media_sidecar_path = str(destination)
        store.put(stored)
    typer.echo(f"Installed media sidecar at {destination}")


@media_app.command("status")
def media_status(profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8") -> None:
    stored = ProfileStore().get(profile)
    binary = discover_media_binary(stored.media_sidecar_path if stored else None)
    typer.echo(
        json.dumps({"installed": binary is not None, "path": str(binary) if binary else None}, indent=2)
    )


@app.command()
def doctor(profile: Annotated[str, typer.Option(help="Profile name.")] = "full-s8") -> None:
    """Report local readiness without contacting Roborock or starting the sidecar."""
    stored = ProfileStore().get(profile)
    checks = {
        "python_supported": (3, 11) <= sys.version_info[:2] < (3, 15),
        "profile_configured": stored is not None,
        "keyring_login_present": CredentialStore().has_login(profile),
        "media_sidecar_present": discover_media_binary(stored.media_sidecar_path if stored else None)
        is not None,
        "ffmpeg_present": shutil.which("ffmpeg") is not None,
    }
    typer.echo(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise typer.Exit(1)


@live_app.command("plan")
def live_plan() -> None:
    """Print the staged HITL plan; execute nothing."""
    typer.echo(render_plan())


@live_app.command("run")
def live_run(
    human_present: Annotated[
        bool,
        typer.Option("--human-present", help="Confirm a human is physically supervising the robot."),
    ] = False,
) -> None:
    """Refuse unattended live execution; orchestration happens collaboratively stage by stage."""
    if not human_present:
        typer.echo("Refusing live validation without --human-present.", err=True)
        raise typer.Exit(2)
    typer.echo(
        "HITL readiness acknowledged. Run stages individually with Codex; "
        "this command intentionally sends no robot command."
    )


def main() -> None:
    app()
