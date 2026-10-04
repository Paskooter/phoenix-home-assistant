"""Small local readers and explicitly configured routine targets.

Query routes never call conversation agents or services. Target IDs and area
names stay inside Home Assistant; only plain answers leave the installation.
"""

import asyncio
import json
import math
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import brightness_supported, color_supported, color_temp_supported
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.color import color_name_to_rgb

from .const import MAX_SHORTCUTS

_DOMAINS = {
    "light": ("light", "lights", "lamp", "lamps"),
    "switch": ("switch", "switches"),
    "fan": ("fan", "fans"),
    "cover": ("blind", "blinds", "curtain", "curtains", "garage door", "garage doors"),
    "lock": ("lock", "locks"),
    "binary_sensor": ("door", "doors", "window", "windows"),
}
_RESERVED = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:volume|sleep|weather|forecast|jokes?|alarms?|timers?)\b",
        r"^(?:stop|cancel|never ?mind|forget it|enough|quit|exit|repeat|say that again)(?: |$)",
        r"^(?:go|turn) (?:back|home)(?: |$)",
        r"^(?:what(?:'s| is)?|tell me|do you know) .*\b(?:time|date|day)\b",
        r"^(?:turn|switch) (?:it )?(?:up|down)$",
        r"^(?:louder|quieter|mute|unmute)(?: |$)",
        r"^(?:help me )?(?:set ?up|connect|pair|configure|delete|remove|reset|forget|show).*\b(?:hue|lights?)\b",
        r"\b(?:hue|lights?)\b.*\b(?:setup|set up|pairing|configuration)\b",
        r"^(?:good ?night|goodbye|bye|yes|no|okay|ok|thanks?|thank you|red|green|blue|yellow|orange|purple|pink|"
        r"white|black|warm white|cool white|cyan|magenta)$",
        r"^(?:cancel|stop|never\s?mind|forget it|help|go to sleep|sleep|wake up|be quiet|quiet|shut up|mute|"
        r"unmute|listen|look at me|come here)(?:\b|$)",
        r"\b(?:volume|your (?:voice|camera)|hue (?:setup|bridge|pairing)|pair (?:with )?hue)\b",
        r"^(?:what(?:'s| is) (?:the )?(?:time|date)|tell (?:me )?(?:a )?joke|(?:take|snap) (?:a )?(?:photo|picture)|"
        r"(?:show|tell) (?:me )?(?:the )?weather|how(?:'s| is) (?:the )?weather|(?:set|cancel|stop|delete) "
        r"(?:a |an |the |my )?(?:alarm|timer)|(?:play|pause|resume|skip) (?:music|a song|the song)|"
        r"(?:connect|disconnect|set up|reset|pair) (?:to )?(?:hue|wifi|wi-fi|bluetooth)|(?:ask|tell) home assistant\b)",
    )
)


def normalize_phrase(value: str) -> str:
    """The owner and Gateway agree on an exact case/whitespace insensitive phrase."""
    return " ".join(value.replace("’", "'").strip().rstrip(".!?").strip().lower().split())


def validate_shortcuts(value: Any) -> list[dict[str, str]]:
    """Validate local options without putting HA entity IDs on the wire."""
    if not isinstance(value, list) or len(value) > MAX_SHORTCUTS:
        raise ValueError("invalid_shortcut")
    shortcuts: list[dict[str, str]] = []
    phrases: set[str] = set()
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid_shortcut")
        phrase = item.get("phrase")
        entity_id = item.get("entity_id")
        try:
            shortcut_id = str(UUID(item["id"]))
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise ValueError("invalid_shortcut") from err
        if (
            not isinstance(phrase, str)
            or not 1 <= len(phrase) <= 80
            or any(ord(char) < 32 or ord(char) == 127 or char in "<>" for char in phrase)
            or not isinstance(entity_id, str)
            or not re.fullmatch(r"(?:scene|script)\.[a-z0-9_]+", entity_id)
        ):
            raise ValueError("invalid_shortcut")
        phrase = normalize_phrase(phrase)
        ordinary = re.sub(r"^(?:hey )?jibo[, ]+", "", phrase)
        ordinary = re.sub(r"^please ", "", ordinary)
        if (
            not phrase
            or any(pattern.search(ordinary) for pattern in _RESERVED)
            or phrase in phrases
            or shortcut_id in ids
        ):
            raise ValueError("invalid_shortcut")
        phrases.add(phrase)
        ids.add(shortcut_id)
        shortcuts.append({"id": shortcut_id, "phrase": phrase, "entity_id": entity_id})
    # Leave room in the 8192-byte frame for all 32 bounded follow-up hints.
    wire_shortcuts = [{"id": item["id"], "phrase": item["phrase"]} for item in shortcuts]
    if len(json.dumps(wire_shortcuts).encode("utf-8")) > 4500:
        raise ValueError("invalid_shortcut")
    return shortcuts


def exposed(hass: HomeAssistant, entity_id: str) -> bool:
    """Check the live Assist exposure setting, including its HA default."""
    return exposed_entities.async_should_expose(hass, "conversation", entity_id)


def device_area(hass: HomeAssistant, device_id: str | None) -> str | None:
    """Resolve an owner-assigned device area without accepting remote claims."""
    if device_id and (device := dr.async_get(hass).async_get(device_id)):
        if resolve := getattr(dr, "async_get_effective_area_id", None):
            return resolve(hass, device)
        return device.area_id  # HA 2026.8 predates inherited child-device areas.
    return None


def _state_area(hass: HomeAssistant, state: State) -> str | None:
    registry = er.async_get(hass)
    if entry := registry.async_get(state.entity_id):
        if entry.area_id:
            return entry.area_id
        return device_area(hass, entry.device_id)
    return None


def _names(hass: HomeAssistant, state: State) -> set[str]:
    names = {normalize_phrase(state.name)}
    if entry := er.async_get(hass).async_get(state.entity_id):
        names.update(normalize_phrase(name) for name in er.async_get_entity_aliases(hass, entry))
        names.update(normalize_phrase(name) for name in (entry.name, entry.original_name) if isinstance(name, str))
    return names


def state_domain(state: State) -> str:
    return state.entity_id.split(".", 1)[0]


def _resolve(
    hass: HomeAssistant,
    target: str,
    device_id: str | None,
    prior_targets: tuple[str, ...],
    measurement: str | None = None,
) -> tuple[list[State], str | None]:
    target = re.sub(r"^the\s+", "", normalize_phrase(target))
    domains = set(_DOMAINS)
    if measurement:
        domains = {"sensor", "climate"}
    candidates = [
        state for state in hass.states.async_all() if state_domain(state) in domains and exposed(hass, state.entity_id)
    ]
    if target in ("it", "they", "them", "those", "that"):
        if not prior_targets:
            return [], "no_context"
        states = [hass.states.get(entity_id) for entity_id in prior_targets]
        if any(state is None or not exposed(hass, state.entity_id) for state in states):
            return [], "no_valid_targets"
        candidates = [state for state in states if state is not None and state_domain(state) in domains]
    else:
        exact = [state for state in candidates if target in _names(hass, state)]
        if len(exact) > 1:
            return [], "ambiguous_target"
        if exact:
            candidates = exact
        else:
            domain = None
            area_phrase = target
            for candidate_domain, words in _DOMAINS.items():
                for word in sorted(words, key=len, reverse=True):
                    if target == word:
                        domain, area_phrase = candidate_domain, "here"
                        break
                    if target.endswith(" " + word):
                        domain, area_phrase = candidate_domain, target[: -len(word)].strip()
                        break
                    if target.startswith(word + " in "):
                        domain, area_phrase = candidate_domain, target[len(word) + 4 :].strip()
                        break
                    if target in (word + " here", word + " this room"):
                        domain, area_phrase = candidate_domain, "here"
                        break
                if domain:
                    break
            area_phrase = re.sub(r"^the\s+", "", area_phrase)
            if area_phrase in ("here", "in here", "this room", "in this room"):
                area_id = device_area(hass, device_id)
            else:
                areas = [
                    area
                    for area in ar.async_get(hass).async_list_areas()
                    if area_phrase in {normalize_phrase(area.name), *(normalize_phrase(a) for a in area.aliases)}
                ]
                if len(areas) > 1:
                    return [], "ambiguous_target"
                area_id = areas[0].id if areas else None
            if area_id is None:
                return [], "no_valid_targets"
            candidates = [state for state in candidates if _state_area(hass, state) == area_id]
            if domain:
                candidates = [state for state in candidates if state_domain(state) == domain]
                if domain == "binary_sensor":
                    classes = {"window"} if "window" in target else {"door", "garage_door", "opening"}
                    candidates = [state for state in candidates if state.attributes.get("device_class") in classes]
                elif domain == "cover":
                    classes = (
                        {"garage", "door"}
                        if "garage door" in target
                        else ({"curtain"} if "curtain" in target else {"blind", "shade", "shutter"})
                    )
                    candidates = [state for state in candidates if state.attributes.get("device_class") in classes]
    if measurement:
        candidates = [
            state
            for state in candidates
            if (state_domain(state) == "sensor" and state.attributes.get("device_class") == measurement)
            or (state_domain(state) == "climate" and state.attributes.get("current_" + measurement) is not None)
        ]
        if len(candidates) > 1:
            return [], "ambiguous_target"
    if not candidates:
        return [], "no_valid_targets"
    if any(state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) for state in candidates):
        return [], "target_unavailable"
    return candidates, None


def _error(code: str) -> dict[str, Any]:
    return {"outcome": "error", "response_type": "error", "speech": "", "code": code}


def read_query(
    hass: HomeAssistant,
    text: str,
    device_id: str | None,
    prior_targets: tuple[str, ...] = (),
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Read one bounded set of exposed current states; never execute an intent."""
    text = normalize_phrase(text).replace("what's ", "what is ")
    text = re.sub(r"^(?:and )?what about (?:the )?(.+)$", r"what is the \1 state", text)
    predicate = None
    measurement = None
    if match := re.fullmatch(r"(?:is|are) (.+?) (?:still )?(on|off|open|closed|locked|unlocked)", text):
        target, predicate = match.groups()
    elif match := re.fullmatch(r"what is (?:the )?(temperature|humidity) (?:in|of) (.+)", text):
        measurement, target = match.groups()
    elif match := re.fullmatch(r"what is (?:the )?(.+?) (temperature|humidity)(?: state)?", text):
        target, measurement = match.groups()
    elif match := re.fullmatch(r"what is (?:the )?(.+?) state", text):
        target = match[1]
    elif match := re.fullmatch(r"what is (?:the )?state of (?:the )?(.+?)", text):
        target = match[1]
    else:
        return _error("unsupported_query"), ()
    states, error = _resolve(hass, target, device_id, prior_targets, measurement)
    if error:
        return _error(error), ()
    if measurement:
        state = states[0]
        value = state.attributes.get("current_" + measurement) if state_domain(state) == "climate" else state.state
        try:
            numeric = float(value)
        except TypeError, ValueError:
            return _error("target_unavailable"), ()
        if not (-1000 < numeric < 100000):
            return _error("target_unavailable"), ()
        unit = "%" if measurement == "humidity" else str(state.attributes.get("unit_of_measurement", ""))
        if state_domain(state) == "climate" and measurement == "temperature":
            unit = str(hass.config.units.temperature_unit)
        speech = f"The {measurement} is {numeric:g}{unit}."
    else:
        values = []
        for state in states:
            value = state.state
            if state_domain(state) == "binary_sensor":
                if state.attributes.get("device_class") not in ("door", "garage_door", "opening", "window"):
                    return _error("unsupported_query"), ()
                value = {"on": "open", "off": "closed"}.get(value, value)
            # Only report the requested simple state vocabulary.
            if value not in ("on", "off", "open", "closed", "opening", "closing", "locked", "unlocked"):
                return _error("unsupported_query"), ()
            predicate_domains = {
                "on": {"light", "switch", "fan"},
                "off": {"light", "switch", "fan"},
                "open": {"binary_sensor", "cover"},
                "closed": {"binary_sensor", "cover"},
                "locked": {"lock"},
                "unlocked": {"lock"},
            }
            if predicate and state_domain(state) not in predicate_domains[predicate]:
                return _error("unsupported_query"), ()
            values.append(value)
        unique = sorted(set(values))
        speech = (
            f"It is {unique[0]}."
            if len(states) == 1
            else (f"They are all {unique[0]}." if len(unique) == 1 else "Their states are mixed.")
        )
        if predicate and len(unique) == 1:
            speech = ("Yes. " if unique[0] == predicate else "No. ") + speech
    return {
        "outcome": "success",
        "response_type": "query_answer",
        "speech": speech,
        "success_count": len(states),
        "failed_count": 0,
    }, tuple(state.entity_id for state in states)


def read_room_follow_up(
    hass: HomeAssistant,
    text: str,
    device_id: str | None,
    prior_targets: tuple[str, ...],
    subject: str | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """A room ellipsis preserves the prior resolved target kind without acting."""
    match = re.fullmatch(r"and in (?:the )?(.+)", normalize_phrase(text))
    if not match or not prior_targets:
        return _error("no_context"), ()
    states = [hass.states.get(entity_id) for entity_id in prior_targets]
    if any(state is None or not exposed(hass, state.entity_id) for state in states):
        return _error("no_valid_targets"), ()
    domains = {state_domain(state) for state in states}
    if len(domains) != 1:
        return _error("ambiguous_target"), ()
    domain = next(iter(domains))
    if domain in ("sensor", "climate"):
        measurement = subject or (states[0].attributes.get("device_class") if domain == "sensor" else None)
        if measurement not in ("temperature", "humidity"):
            return _error("unsupported_query"), ()
        return read_query(hass, f"what is the {match[1]} {measurement}", device_id)
    if domain not in _DOMAINS:
        return _error("unsupported_query"), ()
    noun = _DOMAINS[domain][1] if domain not in ("binary_sensor", "cover") else None
    if domain in ("binary_sensor", "cover"):
        classes = {state.attributes.get("device_class") for state in states}
        if len(classes) != 1:
            return _error("ambiguous_target"), ()
        noun = {
            "window": "windows",
            "door": "doors",
            "opening": "doors",
            "garage_door": "doors",
            "garage": "garage doors",
            "blind": "blinds",
            "shade": "blinds",
            "shutter": "blinds",
            "curtain": "curtains",
        }.get(next(iter(classes)))
        if domain == "cover" and next(iter(classes)) == "door":
            noun = "garage doors"
        if not noun:
            return _error("unsupported_query"), ()
    return read_query(hass, f"what is the {match[1]} {noun} state", device_id)


def query_subject(text: str) -> str | None:
    """Remember the bounded property, without retaining the question text."""
    for subject in ("temperature", "humidity"):
        if re.search(r"\b" + subject + r"\b", normalize_phrase(text)):
            return subject
    return None


def resolved_targets(answer: Any) -> tuple[str, ...]:
    """Retain only HA-resolved successful entity targets, bounded and in memory."""
    return tuple(
        dict.fromkeys(target.id for target in answer.response.success_results if target.type == "entity" and target.id)
    )


def pronoun_action(text: str) -> str | None:
    """A direct follow-up action is intentionally limited to explicit on/off."""
    text = re.sub(r"^please ", "", normalize_phrase(text))
    match = re.fullmatch(r"(?:turn|switch) (?:it|them|those) (on|off)", text)
    if not match:
        match = re.fullmatch(r"(?:turn|switch) (on|off) (?:it|them|those)", text)
    return match[1] if match else None


def implicit_room_command(text: str) -> bool:
    """Bare device groups need a robot area instead of HA's all-domain fallback."""
    text = re.sub(r"^please ", "", normalize_phrase(text))
    if re.search(r"\b(?:here|this room)\b", text):
        return True
    group = r"(?:the )?(?:lights?|lamps?|switch(?:es)?)"
    return any(
        re.fullmatch(pattern, text)
        for pattern in (
            rf"(?:turn|switch) (?:on|off) {group}",
            rf"(?:turn|switch) {group} (?:on|off)",
            rf"(?:set|change) {group}(?: (?:brightness|color))? to .+",
            rf"(?:dim|brighten|lower|raise) {group}(?: brightness)?(?: (?:fully|completely))?",
        )
    )


def pronoun_light_settings(text: str) -> dict[str, Any] | None:
    """Accept the same explicit bounded brightness/color follow-ups as Gateway."""
    text = re.sub(r"^please ", "", normalize_phrase(text))
    match = re.fullmatch(r"(?:set|change) (?:it|them|those)(?: brightness)? to (.+)", text)
    if not match:
        return None
    if percent := re.fullmatch(r"(100|[1-9]?\d) ?(?:percent|%)", match[1]):
        return {"brightness": round(int(percent[1]) * 255 / 100)}
    if match[1] in ("warm white", "cool white"):
        return {"color_temp_kelvin": 2700 if match[1] == "warm white" else 6500}
    if match[1] in ("red", "green", "blue", "yellow", "orange", "purple", "pink", "white", "cyan", "magenta"):
        return {"rgb_color": tuple(color_name_to_rgb(match[1]))}
    return None


def relative_brightness_step(text: str) -> int | None:
    """Only these six phrases adjust an already resolved light's observed level."""
    match = re.fullmatch(r"make (?:it|them|those) (dimmer|brighter)", normalize_phrase(text))
    return (-26 if match[1] == "dimmer" else 26) if match else None


def _observed_brightness(state: State) -> float | None:
    value = state.attributes.get("brightness")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 255 and math.isfinite(value):
        return value
    return None


async def apply_relative_brightness(
    hass: HomeAssistant,
    targets: tuple[str, ...],
    step: int,
    context: Context,
    remaining: Callable[[], float],
) -> dict[str, Any]:
    """Preflight the whole cached group, issue each absolute level once, confirm it."""
    if not targets:
        return _error("no_context")
    if len(targets) > 32 or len(set(targets)) != len(targets):
        return _error("ambiguous_target")
    if step not in (-26, 26):
        return _error("unsupported_feature")

    def target_error(entity_id: str) -> str | None:
        if (state := hass.states.get(entity_id)) is None or not exposed(hass, entity_id):
            return "no_valid_targets"
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return "target_unavailable"
        modes = state.attributes.get("supported_color_modes")
        if (
            state_domain(state) != "light"
            or not isinstance(modes, (list, tuple, set, frozenset))
            or any(not isinstance(mode, str) for mode in modes)
            or not brightness_supported(modes)
        ):
            return "unsupported_feature"
        if state.state != "on" or _observed_brightness(state) is None:
            return "brightness_unavailable"
        return None

    # No await or service call until every target has an exposed, available,
    # brightness-supported ON state with a finite, bounded observed level.
    plan: list[tuple[str, float, int]] = []
    for entity_id in targets:
        if code := target_error(entity_id):
            return _error(code)
        observed = _observed_brightness(hass.states.get(entity_id))
        plan.append((entity_id, observed, max(0, min(255, round(observed + step)))))

    issued: set[str] = set()
    confirmed: set[str] = set()

    def matches(entity_id: str, expected: int) -> bool:
        if (state := hass.states.get(entity_id)) is None or not exposed(hass, entity_id):
            return False
        if expected == 0:
            return state.state == "off"
        observed = _observed_brightness(state)
        return state.state == "on" and observed is not None and abs(observed - expected) <= 1

    def failure(code: str) -> dict[str, Any]:
        if not issued:
            return {**_error(code), "outcome": "expired" if code == "expired" else "error"}
        count = sum(entity_id in confirmed and matches(entity_id, value) for entity_id, _, value in plan)
        return {
            "outcome": "partial" if count else "uncertain",
            "response_type": "action_done" if count else "error",
            "speech": "Some brightness changes were confirmed; I could not confirm the rest." if count else "",
            "code": "confirmation_lost",
            "success_count": count,
            "failed_count": len(plan) - count,
        }

    try:
        budget = remaining()
        if budget <= 0:
            return failure("expired")
        # This single budget covers every service await and every confirmation.
        async with asyncio.timeout(budget):
            for index, (entity_id, _, expected) in enumerate(plan):
                if remaining() <= 0:
                    return failure("expired")
                if any(not exposed(hass, target) or hass.states.get(target) is None for target in targets):
                    return failure("no_valid_targets")
                if any(hass.states.get(target).state in (STATE_UNAVAILABLE, STATE_UNKNOWN) for target in targets):
                    return failure("target_unavailable")
                if any(target in confirmed and not matches(target, value) for target, _, value in plan):
                    return failure("confirmation_lost")
                # An off/revoked/changed later target cannot be turned on by a
                # stale relative step after an earlier target has changed.
                for pending_id, observed, _ in plan[index:]:
                    if code := target_error(pending_id):
                        return failure(code)
                    if _observed_brightness(hass.states.get(pending_id)) != observed:
                        return failure("brightness_unavailable")
                issued.add(entity_id)
                await hass.services.async_call(
                    "light", "turn_on", {"entity_id": entity_id, "brightness": expected}, blocking=True, context=context
                )
                while not matches(entity_id, expected):
                    if any(
                        (state := hass.states.get(target)) is None
                        or not exposed(hass, target)
                        or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                        for target in targets
                    ):
                        return failure("confirmation_lost")
                    await asyncio.sleep(0.05)
                confirmed.add(entity_id)
            if not all(matches(entity_id, expected) for entity_id, _, expected in plan):
                return failure("confirmation_lost")
    except asyncio.CancelledError:
        raise  # Socket loss/unload never resumes or replays the remaining targets.
    except Exception:
        return failure("confirmation_lost")
    return {
        "outcome": "success",
        "response_type": "action_done",
        "speech": "Done.",
        "success_count": len(plan),
        "failed_count": 0,
    }


async def apply_follow_up(
    hass: HomeAssistant,
    targets: tuple[str, ...],
    action: str,
    context,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reuse resolved exposed targets once, then confirm simple on/off state."""
    if not targets:
        return _error("no_context")
    states = [hass.states.get(entity_id) for entity_id in targets]
    if any(state is None or not exposed(hass, state.entity_id) for state in states):
        return _error("no_valid_targets")
    if any(state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) for state in states):
        return _error("target_unavailable")
    if any(state_domain(state) not in ("light", "switch", "fan") for state in states):
        return _error("no_valid_targets")
    if parameters and any(state_domain(state) != "light" for state in states):
        return _error("no_valid_targets")
    if parameters:
        # HA can silently drop unsupported turn_on parameters. Validate every
        # live target before dispatch so an unsupported color cannot turn an
        # off light on, or leave a mixed group partially changed.
        checks = {
            "brightness": brightness_supported,
            "rgb_color": color_supported,
            "color_temp_kelvin": color_temp_supported,
        }
        for state in states:
            modes = state.attributes.get("supported_color_modes")
            if any(key not in checks or not checks[key](modes) for key in parameters):
                return _error("unsupported_feature")
            if "color_temp_kelvin" in parameters:
                lower = state.attributes.get("min_color_temp_kelvin")
                upper = state.attributes.get("max_color_temp_kelvin")
                if (
                    any(
                        not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                        for value in (lower, upper)
                    )
                    or not 0 < lower <= parameters["color_temp_kelvin"] <= upper
                ):
                    return _error("unsupported_feature")
    for domain in sorted({state_domain(state) for state in states}):
        await hass.services.async_call(
            domain,
            "turn_" + action,
            {"entity_id": [state.entity_id for state in states if state_domain(state) == domain], **(parameters or {})},
            blocking=True,
            context=context,
        )
    return {
        "outcome": "success",
        "response_type": "action_done",
        "speech": "Done.",
        "success_count": len(states),
        "failed_count": 0,
    }
