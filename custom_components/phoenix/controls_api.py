"""Bounded media and camera access over the already paired, pinned TLS endpoint."""

import asyncio
import json
from pathlib import Path

from aiohttp import ClientError, ClientTimeout
from homeassistant.components import media_source
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN
from .controls import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES
from .local_api import LocalFingerprint, canonical_uuid, hexadecimal, normalize_endpoint

IMAGE_TYPES = frozenset(("image/png", "image/jpeg"))
AUDIO_TYPES = frozenset(("audio/wav", "audio/x-wav", "audio/wave"))


def _media_error(key: str = "invalid_control_media", *, uncertain: bool = False) -> None:
    error = HomeAssistantError if uncertain else ServiceValidationError
    raise error(translation_domain=DOMAIN, translation_key=key)


def _read_local_media(path: Path, media_dirs: dict[str, str], maximum: int) -> bytes:
    """Read only configured local media roots, including resolved symlink checks."""
    path = path.resolve(strict=True)
    if not any(path.is_relative_to(Path(directory).resolve()) for directory in media_dirs.values()):
        raise ValueError("Media is outside the configured media directories")
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("Invalid media size")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if not 0 < len(data) <= maximum:
        raise ValueError("Invalid media size")
    return data


def _matches_type(data: bytes, content_type: str) -> bool:
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    return content_type in AUDIO_TYPES and data.startswith(b"RIFF") and data[8:12] == b"WAVE"


def _endpoint(client, suffix: str):
    return (
        normalize_endpoint(client.entry.data["host"], client.entry.data["port"]) + "/phoenix/local/v1" + suffix,
        LocalFingerprint(bytes.fromhex(hexadecimal(client.entry.data["fingerprint"]))),
        {"Authorization": "Bearer " + hexadecimal(client.entry.data["credential"])},
    )


async def _read_response(response, maximum: int) -> bytes:
    """A chunked peer cannot bypass the size bound or return a partial prefix."""
    if response.content_length is not None and response.content_length > maximum:
        _media_error("control_media_upload_failed")
    chunks = []
    size = 0
    async for chunk in response.content.iter_chunked(min(maximum + 1, 64 * 1024)):
        size += len(chunk)
        if size > maximum:
            _media_error("control_media_upload_failed")
        chunks.append(chunk)
    return b"".join(chunks)


async def async_upload_media(client, robot_id: str, media_content_id: str, kind: str, entity_id: str) -> str:
    """Resolve local HA media and upload bytes once; never proxy arbitrary URLs."""
    feature = "screen_image" if kind == "image" else "audio_playback"
    action = "display_image" if kind == "image" else "play_audio"
    if code := client._control_admission_error(robot_id, feature, action):
        client._control_error(code)
    if not isinstance(media_content_id, str) or not media_content_id.startswith("media-source://media_source/"):
        _media_error()
    try:
        resolved = await media_source.async_resolve_media(client.hass, media_content_id, target_media_player=entity_id)
        allowed = IMAGE_TYPES if kind == "image" else AUDIO_TYPES
        if resolved.mime_type not in allowed or not isinstance(resolved.path, Path):
            _media_error()
        maximum = MAX_IMAGE_BYTES if kind == "image" else MAX_AUDIO_BYTES
        data = await client.hass.async_add_executor_job(
            _read_local_media, resolved.path, client.hass.config.media_dirs, maximum
        )
        if not _matches_type(data, resolved.mime_type):
            _media_error()
    except OSError, ValueError, media_source.MediaSourceError:
        _media_error()
    # Resolving/reading can await. Permission, quiet hours, touch, and online
    # status are rechecked before sending any private media to the robot.
    if code := client._control_admission_error(robot_id, feature, action):
        client._control_error(code)
    endpoint, fingerprint, headers = _endpoint(client, "/media")
    content_type = "audio/wav" if resolved.mime_type in AUDIO_TYPES else resolved.mime_type
    headers["Content-Type"] = content_type
    try:
        async with client.session.post(
            endpoint,
            data=data,
            headers=headers,
            ssl=fingerprint,
            allow_redirects=False,
            timeout=ClientTimeout(total=30),
        ) as response:
            if response.status != 201:
                _media_error("control_media_upload_failed")
            result_bytes = await _read_response(response, 8192)
            result = json.loads(result_bytes)
            media_id = canonical_uuid(result["media_id"])
            if result.get("content_type") != content_type or result.get("size") != len(data):
                _media_error("control_media_upload_failed")
            return media_id
    except ClientError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError:
        _media_error("control_media_upload_failed")


async def async_camera_image(client, robot_id: str) -> bytes:
    """Read an active native capture; opening a dashboard never starts a camera."""
    if (
        not client.control_available(robot_id, "camera_streaming")
        or client.observed_control("camera_active") is not True
    ):
        _media_error("control_camera_inactive")
    endpoint, fingerprint, headers = _endpoint(client, "/camera.jpg")
    try:
        async with asyncio.timeout(10):
            async with client.session.get(
                endpoint,
                headers=headers,
                ssl=fingerprint,
                allow_redirects=False,
                timeout=ClientTimeout(total=10),
            ) as response:
                if response.status != 200 or response.content_type != "image/jpeg":
                    _media_error("control_camera_inactive")
                data = await _read_response(response, MAX_IMAGE_BYTES)
                if not 0 < len(data) <= MAX_IMAGE_BYTES or not _matches_type(data, "image/jpeg"):
                    _media_error("control_camera_inactive")
                if (
                    not client.control_available(robot_id, "camera_streaming")
                    or client.observed_control("camera_active") is not True
                ):
                    _media_error("control_camera_inactive")
                return data
    except ClientError, ConnectionError, TimeoutError, ValueError, TypeError, KeyError:
        _media_error("control_camera_inactive")
