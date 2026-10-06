"""Bounded, negotiated robot controls; never infer a missing observation."""

import math
from typing import Any

from .const import (
    CONF_ALLOW_AUDIO,
    CONF_ALLOW_CAMERA,
    CONF_ALLOW_RING_LIGHT,
    CONF_ALLOW_SCREEN,
    CONF_ALLOW_SKILLS,
    CONF_ALLOW_SLEEP,
)

CONTROL_STATE_SECONDS = 60
CONTROL_SECONDS = 30
MAX_IMAGE_BYTES = 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_MEDIA_BYTES = MAX_AUDIO_BYTES
MAX_SCREEN_TEXT = 300
MAX_SKILLS = 32
CONTROL_OPTIONS = {
    "screen": CONF_ALLOW_SCREEN,
    "ring_light": CONF_ALLOW_RING_LIGHT,
    "audio": CONF_ALLOW_AUDIO,
    "sleep": CONF_ALLOW_SLEEP,
    "skills": CONF_ALLOW_SKILLS,
    "camera": CONF_ALLOW_CAMERA,
}
CONTROL_FEATURES = {
    "screen_text": "screen",
    "screen_image": "screen",
    "ring_color": "ring_light",
    "speaker_volume": "audio",
    "audio_playback": "audio",
    "sleep_control": "sleep",
    "installed_skills": "skills",
    "camera_streaming": "camera",
}
CONTROL_ACTIONS = {
    "display_text": "screen_text",
    "display_image": "screen_image",
    "clear_screen": "screen_text",
    "set_ring_color": "ring_color",
    "ring_off": "ring_color",
    "set_volume": "speaker_volume",
    "play_audio": "audio_playback",
    "stop_audio": "audio_playback",
    "pause_audio": "audio_playback",
    "resume_audio": "audio_playback",
    "sleep": "sleep_control",
    "wake": "sleep_control",
    "run_skill": "installed_skills",
    "stop": "installed_skills",
    "start_camera": "camera_streaming",
    "stop_camera": "camera_streaming",
}


def checked_names(value: Any, allowed: dict[str, Any]) -> set[str]:
    """Negotiate only a bounded known feature/group list."""
    if (
        not isinstance(value, list)
        or len(value) > 32
        or any(not isinstance(item, str) or len(item) > 50 for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("Invalid control capability list")
    return set(value).intersection(allowed)


def plain_text(value: Any, limit: int, *, empty: bool = False) -> bool:
    """Reject markup control characters, preserving ordinary Unicode text."""
    return bool(
        isinstance(value, str)
        and (empty or value.strip())
        and len(value) <= limit
        and not any(ord(char) < 32 and char not in "\n\t" for char in value)
    )


def rgb(value: Any) -> tuple[int, int, int] | None:
    """Only genuine three-channel readings count as a color."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    if any(not isinstance(part, int) or isinstance(part, bool) or not 0 <= part <= 255 for part in value):
        return None
    return tuple(value)


def percent(value: Any) -> int | float | None:
    """Missing, nonfinite and out-of-range volume readings stay unknown."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100:
        return value
    return None


def validate_control_state(value: Any) -> dict[str, Any]:
    """Validate a native snapshot without turning absent values into off/idle."""
    if not isinstance(value, dict) or len(value) > 24:
        raise ValueError("Invalid control snapshot")
    checked: dict[str, Any] = {}
    # Forward-compatible snapshots may add fields. Read only known, bounded
    # values and never retain arbitrary native data in HA state/diagnostics.
    checked["speaker_volume_percent"] = percent(value.get("speaker_volume_percent"))
    checked["ring_rgb"] = rgb(value.get("ring_rgb"))
    for key in ("sleeping", "screen_active", "ring_active", "camera_active"):
        checked[key] = value[key] if isinstance(value.get(key), bool) else None
    checked["screen_text"] = (
        value.get("screen_text") if plain_text(value.get("screen_text"), MAX_SCREEN_TEXT, empty=True) else None
    )
    checked["audio_state"] = (
        value.get("audio_state") if value.get("audio_state") in ("idle", "loading", "playing", "paused") else None
    )
    for key in ("screen_image_id", "audio_media_id", "active_skill_id"):
        checked[key] = value[key] if plain_text(value.get(key), 160) else None
    skills = value.get("installed_skills")
    if skills is None:
        checked["installed_skills"] = None
    elif isinstance(skills, list) and len(skills) <= MAX_SKILLS:
        seen = set()
        checked_skills = []
        for skill in skills:
            if (
                not isinstance(skill, dict)
                or not plain_text(skill.get("id"), 160)
                or not plain_text(skill.get("name"), 100)
                or skill["id"] in seen
            ):
                raise ValueError("Invalid installed skill catalog")
            seen.add(skill["id"])
            checked_skills.append({"id": skill["id"], "name": skill["name"]})
        checked["installed_skills"] = checked_skills
    else:
        raise ValueError("Invalid installed skill catalog")
    return checked


def validate_action(action: str, payload: Any, catalog: list[dict[str, str]] | None) -> None:
    """Entity helpers cannot expand into arbitrary native method invocation."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid control payload")
    fields = {
        "display_text": {"text", "duration_ms"},
        "display_image": {"media_id", "duration_ms"},
        "set_ring_color": {"rgb", "duration_ms"},
        "set_volume": {"volume_percent"},
        "play_audio": {"media_id"},
        "run_skill": {"skill_id"},
        "start_camera": {"duration_ms"},
    }
    if action not in CONTROL_ACTIONS or set(payload).difference(fields.get(action, set())):
        raise ValueError("Unsupported control payload")
    if "duration_ms" in payload and (
        not isinstance(payload["duration_ms"], int)
        or isinstance(payload["duration_ms"], bool)
        or not 1 <= payload["duration_ms"] <= 60_000
    ):
        raise ValueError("Invalid duration")
    if action == "display_text" and not plain_text(payload.get("text"), MAX_SCREEN_TEXT):
        raise ValueError("Invalid screen text")
    if action == "set_ring_color" and rgb(payload.get("rgb")) is None:
        raise ValueError("Invalid ring color")
    if action == "set_volume" and percent(payload.get("volume_percent")) is None:
        raise ValueError("Invalid volume")
    if action in ("display_image", "play_audio"):
        from .local_api import canonical_uuid

        canonical_uuid(payload.get("media_id"))
    if action == "run_skill" and not any(item["id"] == payload.get("skill_id") for item in (catalog or [])):
        raise ValueError("Skill is not in the current installed catalog")
