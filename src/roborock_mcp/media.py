"""Loopback-only lifecycle manager for the patched go2rtc sidecar."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from roborock_mcp.errors import DomainError, ErrorCode


@dataclass(slots=True)
class MediaSession:
    key: str
    stream: str
    viewer_url: str
    expires_at_monotonic: float
    mode: str


class MediaSupervisor:
    """Start a private sidecar and manage short-lived streams in memory."""

    def __init__(self, binary: Path, *, port: int | None = None) -> None:
        self.binary = binary
        self.port = port or _free_loopback_port()
        self.username = "roborockmcp"
        self.password = secrets.token_urlsafe(24)
        self.process: asyncio.subprocess.Process | None = None
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._sessions: dict[str, MediaSession] = {}
        self._expiry_tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise DomainError(ErrorCode.MEDIA_UNAVAILABLE, f"Media sidecar is not executable: {self.binary}")
        self._tempdir = tempfile.TemporaryDirectory(prefix="roborockmcp-media-")
        config_path = Path(self._tempdir.name) / "go2rtc.yaml"
        config_path.write_text(
            "api:\n"
            f"  listen: 127.0.0.1:{self.port}\n"
            f"  username: {self.username}\n"
            f"  password: {self.password}\n"
            "webrtc:\n"
            "  listen: 127.0.0.1:0\n"
            "log:\n"
            "  level: warn\n",
            encoding="utf-8",
        )
        os.chmod(config_path, 0o600)
        self.process = await asyncio.create_subprocess_exec(
            str(self.binary),
            "-config",
            str(config_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._wait_until_ready()

    async def open(self, source_uri: str, *, mode: str, quality: str, ttl_seconds: int) -> MediaSession:
        await self.start()
        if self._sessions:
            raise DomainError(ErrorCode.INVALID_STATE, "Only one live media session is allowed per process.")
        key = secrets.token_urlsafe(24)
        stream = f"roborock-{secrets.token_hex(12)}"
        async with self._session() as client:
            async with client.patch(self._url("/api/streams", {"src": stream}), data=source_uri) as response:
                if response.status >= 300:
                    raise DomainError(
                        ErrorCode.MEDIA_UNAVAILABLE,
                        f"Sidecar rejected stream registration ({response.status}).",
                    )
        media = "video+audio+microphone" if mode == "voice_chat" else "video"
        viewer_url = self._url(
            "/webrtc.html", {"src": stream, "media": media, "quality": quality}, browser_auth=True
        )
        session = MediaSession(
            key=key,
            stream=stream,
            viewer_url=viewer_url,
            expires_at_monotonic=asyncio.get_running_loop().time() + ttl_seconds,
            mode=mode,
        )
        self._sessions[key] = session
        self._expiry_tasks[key] = asyncio.create_task(self._expire(key, ttl_seconds))
        return session

    async def close_session(self, key: str) -> bool:
        session = self._sessions.pop(key, None)
        task = self._expiry_tasks.pop(key, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        if not session:
            return False
        try:
            async with self._session() as client:
                async with client.delete(self._url("/api/streams", {"src": session.stream})):
                    pass
        except aiohttp.ClientError:
            pass
        return True

    async def close(self) -> None:
        for key in list(self._sessions):
            await self.close_session(key)
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self._tempdir:
            self._tempdir.cleanup()
            self._tempdir = None

    async def _expire(self, key: str, ttl_seconds: int) -> None:
        await asyncio.sleep(ttl_seconds)
        await self.close_session(key)

    async def _wait_until_ready(self) -> None:
        for _ in range(50):
            if self.process and self.process.returncode is not None:
                raise DomainError(ErrorCode.MEDIA_UNAVAILABLE, "Media sidecar exited during startup.")
            try:
                async with self._session() as client:
                    async with client.get(self._url("/api")) as response:
                        if response.status < 500:
                            return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
        raise DomainError(ErrorCode.MEDIA_UNAVAILABLE, "Media sidecar did not become ready.")

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            auth=aiohttp.BasicAuth(self.username, self.password), raise_for_status=False
        )

    def _url(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        browser_auth: bool = False,
    ) -> str:
        authority = f"127.0.0.1:{self.port}"
        if browser_auth:
            authority = f"{quote(self.username)}:{quote(self.password)}@{authority}"
        base = f"http://{authority}{path}"
        return f"{base}?{urlencode(params)}" if params else base


def discover_media_binary(configured: str | None = None) -> Path | None:
    configured_path = Path(configured).expanduser() if configured is not None else None
    installed = shutil.which("go2rtc-roborockmcp")
    candidates = [
        configured_path,
        Path.cwd() / "build" / "media" / "go2rtc-roborockmcp",
        Path(installed) if installed is not None else None,
    ]
    return next((item for item in candidates if item and item.is_file()), None)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
