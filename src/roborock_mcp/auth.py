"""Out-of-band Roborock OTP authentication and OS-keyring persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass

import keyring
from roborock.data import UserData
from roborock.web_api import RoborockApiClient

from roborock_mcp.config import APP_NAME, Profile, ProfileStore, default_capabilities
from roborock_mcp.errors import DomainError, ErrorCode

_USER_DATA_KEY = "user-data"
_CAMERA_PIN_KEY = "camera-pin"


def _service(profile: str, key: str) -> str:
    return f"{APP_NAME}:{profile}:{key}"


@dataclass(slots=True)
class StoredCredentials:
    username: str
    base_url: str | None
    user_data: UserData
    camera_pin: str | None = None


class CredentialStore:
    def save_login(self, profile: str, username: str, base_url: str | None, user_data: UserData) -> None:
        payload = {"username": username, "base_url": base_url, "user_data": user_data.as_dict()}
        keyring.set_password(_service(profile, _USER_DATA_KEY), profile, json.dumps(payload))

    def load(self, profile: str) -> StoredCredentials:
        raw = keyring.get_password(_service(profile, _USER_DATA_KEY), profile)
        if not raw:
            raise DomainError(ErrorCode.AUTH_REQUIRED, f"Profile '{profile}' is not logged in.")
        payload = json.loads(raw)
        return StoredCredentials(
            username=str(payload["username"]),
            base_url=payload.get("base_url"),
            user_data=UserData.from_dict(payload["user_data"]),
            camera_pin=keyring.get_password(_service(profile, _CAMERA_PIN_KEY), profile),
        )

    def set_camera_pin(self, profile: str, pin: str) -> None:
        if not pin.isdigit() or len(pin) < 4:
            raise ValueError("camera PIN must contain at least four digits")
        keyring.set_password(_service(profile, _CAMERA_PIN_KEY), profile, pin)

    def delete(self, profile: str) -> None:
        for key in (_USER_DATA_KEY, _CAMERA_PIN_KEY):
            try:
                keyring.delete_password(_service(profile, key), profile)
            except keyring.errors.PasswordDeleteError:
                pass

    def has_login(self, profile: str) -> bool:
        return keyring.get_password(_service(profile, _USER_DATA_KEY), profile) is not None


async def request_login_code(username: str) -> RoborockApiClient:
    client = RoborockApiClient(username=username)
    await client.request_code_v4()
    return client


async def complete_login(
    client: RoborockApiClient,
    username: str,
    code: str,
    profile_name: str,
    credentials: CredentialStore,
    profiles: ProfileStore,
) -> Profile:
    user_data = await client.code_login_v4(code)
    base_url = await client.base_url
    credentials.save_login(profile_name, username, base_url, user_data)
    profile = Profile(
        name=profile_name,
        username_hint=_masked_email(username),
        base_url=base_url,
        capabilities=default_capabilities(),
    )
    profiles.put(profile)
    return profile


def _masked_email(username: str) -> str:
    if "@" not in username:
        return "***"
    local, domain = username.split("@", 1)
    return f"{local[:1]}***@{domain}"
