"""Useful HA features over the paired local channel; all devices are invented."""

import asyncio
import math
from datetime import timedelta
from uuid import uuid4

import pytest
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.phoenix.commands import validate_shortcuts
from tests.direct_backend import SyntheticLocalRobot, linked_entry, wait_for
from tests.test_agents import SyntheticGrok, choose_agent
from tests.test_conversation import SyntheticLight, install_devices
from tests.test_transport import allow_announcements


class Temperature(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, name, value):
        self._attr_name, self._attr_unique_id, self._attr_native_value = name, name, value


class HSLight(SyntheticLight):
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_color_mode = ColorMode.HS
    _attr_hs_color = (0, 0)

    async def async_turn_on(self, **kwargs):
        self._attr_hs_color = kwargs.get("hs_color", self._attr_hs_color)
        await super().async_turn_on(**kwargs)


async def devices(hass):
    kitchen, bedroom = await install_devices(hass)
    bedroom_area = ar.async_get(hass).async_create("Bedroom")
    er.async_get(hass).async_update_entity(bedroom.entity_id, area_id=bedroom_area.id)
    assert await async_setup_component(hass, "sensor", {})
    temperatures = [Temperature("Kitchen Temperature", 21), Temperature("Bedroom Temperature", 18)]
    await hass.data["sensor"].async_add_entities(temperatures)
    for sensor, room in zip(temperatures, ["Kitchen", "Bedroom"], strict=True):
        er.async_get(hass).async_update_entity(
            sensor.entity_id, area_id=ar.async_get(hass).async_get_area_by_name(room).id
        )
        exposed_entities.async_expose_entity(hass, "conversation", sensor.entity_id, True)
    return kitchen, bedroom


def assign_room(hass, entry, room):
    robot = entry.runtime_data.client.robots[entry.data["robot_id"]]
    dr.async_get(hass).async_update_device(robot.device_id, area_id=ar.async_get(hass).async_get_area_by_name(room).id)


async def seed(hass, robot, text="are the kitchen lights on"):
    result, _ = await robot.command(text, route={"kind": "query"})
    assert result["outcome"] == "success", result
    return result


async def test_device_area_scopes_here_commands_and_read_only_queries(hass, robot):
    kitchen, bedroom = await devices(hass)
    entry = await linked_entry(hass, robot)
    assign_room(hass, entry, "Kitchen")
    result, _ = await robot.command("turn on the lights here")
    assert result["outcome"] == "success" and len(kitchen.calls) == 1 and bedroom.calls == []
    agent = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([agent])
    await choose_agent(hass, entry, agent.entity_id)
    await wait_for(lambda: entry.runtime_data.client.ready)
    for question in ["are the lights here on", "are the lights in this room on", "what is the kitchen light state"]:
        result = await seed(hass, robot, question)
        assert "on" in result["speech"]
    await seed(hass, robot, "what is the kitchen temperature")
    result, _ = await robot.command("and in bedroom", route={"kind": "follow_up"})
    assert result["outcome"] == "success" and "18" in result["speech"]
    assert agent.calls == [] and len(kitchen.calls) == 1 and bedroom.calls == []
    result, _ = await robot.command("turn on bedroom lights", route={"kind": "query"})
    assert result["outcome"] == "error" and agent.calls == [] and bedroom.calls == []


async def test_unassigned_robot_implicit_room_cannot_expand_to_all_rooms(hass, robot):
    kitchen, bedroom = await devices(hass)
    entry = await linked_entry(hass, robot)
    result, _ = await robot.command("turn on the lights")
    assert result["outcome"] == "error" and result["code"] == "room_not_configured"
    assert not kitchen.calls and not bedroom.calls and not entry.runtime_data.client.contexts


async def test_exact_routine_starts_unknown_scene_and_rechecks_exposure(hass, robot):
    kitchen, bedroom = await devices(hass)
    agent = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await linked_entry(hass, robot)
    assert hass.states.get("scene.dinner").state == "unknown"
    await choose_agent(hass, entry, agent.entity_id, shortcut_phrase="reading time", shortcut_target="scene.dinner")
    await wait_for(lambda: entry.runtime_data.client.ready and robot.preferences.get("shortcuts"))
    shortcut = entry.options["routine_shortcuts"][0]
    assert "entity_id" not in robot.preferences["shortcuts"][0]
    result, _ = await robot.command("Reading time!", route={"kind": "routine", "shortcut_id": shortcut["id"]})
    assert result["outcome"] == "success" and "started" in result["speech"] and not agent.calls
    assert len(kitchen.calls) == 1 and not bedroom.calls and not entry.runtime_data.client.contexts
    exposed_entities.async_expose_entity(hass, "conversation", "scene.dinner", False)
    await wait_for(lambda: robot.preferences.get("shortcuts") == [])
    result, _ = await robot.command("reading time", route={"kind": "routine", "shortcut_id": shortcut["id"]})
    assert result["outcome"] == "error" and len(kitchen.calls) == 1


@pytest.mark.parametrize(
    "phrase",
    ["stop", "sleep", "what time is it", "volume up", "set a timer", "take a photo", "help me pair hue", "blue", "yes"],
)
def test_native_reserved_phrases_cannot_be_owner_routines(phrase):
    with pytest.raises(ValueError, match="invalid_shortcut"):
        validate_shortcuts([{"id": str(uuid4()), "phrase": phrase, "entity_id": "scene.invented"}])


async def test_followup_targets_are_per_entry_and_clear_on_failure_disconnect_and_agent_change(hass, robot, tmp_path):
    kitchen, bedroom = await devices(hass)
    entry = await linked_entry(hass, robot)
    await seed(hass, robot)
    other = await SyntheticLocalRobot(tmp_path / "other-robot").start()
    try:
        other_entry = await linked_entry(hass, other)
        result, _ = await other.command("turn it on", route={"kind": "follow_up"})
        assert result["code"] == "no_context" and not kitchen.calls and not bedroom.calls
        result, _ = await robot.command("turn it on", route={"kind": "follow_up"})
        assert result["outcome"] == "success" and len(kitchen.calls) == 1 and not bedroom.calls
        result, _ = await robot.command("turn on nonexistent lights")
        assert result["outcome"] == "error" and not entry.runtime_data.client.contexts
        result, _ = await robot.command("turn it off", route={"kind": "follow_up"})
        assert result["code"] == "no_context" and len(kitchen.calls) == 1
        await seed(hass, robot)
        await robot.disconnect()
        await wait_for(lambda: not entry.runtime_data.client.ready)
        await wait_for(lambda: entry.runtime_data.client.ready)
        assert not entry.runtime_data.client.contexts and not other_entry.runtime_data.client.contexts
        await seed(hass, robot)
        agent = SyntheticGrok()
        await hass.data["conversation"].async_add_entities([agent])
        await choose_agent(hass, entry, agent.entity_id)
        await wait_for(lambda: entry.runtime_data.client.ready)
        assert not entry.runtime_data.client.contexts and not agent.calls
    finally:
        await other.close()


async def test_actual_thirty_second_context_expiry_and_live_exposure(hass, robot):
    kitchen, _ = await devices(hass)
    entry = await linked_entry(hass, robot)
    await seed(hass, robot)
    await wait_for(lambda: robot.preferences.get("follow_up"))
    await wait_for(lambda: not entry.runtime_data.client.contexts, seconds=31)
    result, _ = await robot.command("make it dimmer", route={"kind": "follow_up"})
    assert result["code"] == "no_context" and not kitchen.calls
    await seed(hass, robot)
    exposed_entities.async_expose_entity(hass, "conversation", kitchen.entity_id, False)
    result, _ = await robot.command("turn it on", route={"kind": "follow_up"})
    assert result["outcome"] == "error" and not kitchen.calls and not entry.runtime_data.client.contexts


@pytest.mark.parametrize(
    "mode,phrase",
    [
        (ColorMode.BRIGHTNESS, "set it to blue"),
        (ColorMode.ONOFF, "set it brightness to 25 percent"),
        (ColorMode.HS, "set it to warm white"),
        (ColorMode.COLOR_TEMP, "set it to cool white"),
    ],
)
async def test_unsupported_cached_light_features_have_no_native_service(hass, robot, mode, phrase):
    kitchen, _ = await devices(hass)
    kitchen._attr_supported_color_modes = {mode}
    kitchen._attr_color_mode = mode
    kitchen._attr_min_color_temp_kelvin, kitchen._attr_max_color_temp_kelvin = 2700, 4000
    kitchen.async_write_ha_state()
    entry = await linked_entry(hass, robot)
    await seed(hass, robot)
    result, _ = await robot.command(phrase, route={"kind": "follow_up"})
    assert result["outcome"] == "error" and result["code"] == "unsupported_feature", result
    assert (
        not kitchen.calls
        and hass.states.get(kitchen.entity_id).state == "off"
        and not entry.runtime_data.client.contexts
    )


async def test_actual_hs_green_and_absolute_brightness_confirm(hass, robot):
    await devices(hass)
    lamp = HSLight("Reading Light", "invented-hs")
    await hass.data["light"].async_add_entities([lamp])
    exposed_entities.async_expose_entity(hass, "conversation", lamp.entity_id, True)
    await linked_entry(hass, robot)
    await seed(hass, robot, "is the reading light on")
    result, _ = await robot.command("set it to green", route={"kind": "follow_up"})
    assert result["outcome"] == "success" and hass.states.get(lamp.entity_id).attributes["hs_color"] == (120, 100), (
        result
    )
    result, _ = await robot.command("set it brightness to 25 percent", route={"kind": "follow_up"})
    assert result["outcome"] == "success" and hass.states.get(lamp.entity_id).attributes["brightness"] == 64
    assert len(lamp.calls) == 2


async def relative_group(hass, robot):
    kitchen, bedroom = await devices(hass)
    reading = SyntheticLight("Kitchen Reading Light", "invented-relative")
    reading._attr_is_on, reading._attr_brightness = True, 80
    kitchen._attr_is_on, kitchen._attr_brightness = True, 200
    kitchen.async_write_ha_state()
    await hass.data["light"].async_add_entities([reading])
    er.async_get(hass).async_update_entity(
        reading.entity_id, area_id=ar.async_get(hass).async_get_area_by_name("Kitchen").id
    )
    exposed_entities.async_expose_entity(hass, "conversation", reading.entity_id, True)
    entry = await linked_entry(hass, robot)
    await seed(hass, robot)
    assert len(entry.runtime_data.client.contexts[robot.robot_id].targets) == 2
    return entry, kitchen, reading, bedroom


async def test_relative_group_uses_absolute_per_target_levels_and_clamps(hass, robot):
    entry, kitchen, reading, bedroom = await relative_group(hass, robot)
    result, _ = await robot.command("make them dimmer", route={"kind": "follow_up"})
    assert (
        result["outcome"] == "success"
        and kitchen.calls == [("on", {"brightness": 174})]
        and reading.calls == [("on", {"brightness": 54})]
    )
    result, _ = await robot.command("make those brighter", route={"kind": "follow_up"})
    assert (
        result["outcome"] == "success"
        and kitchen.calls[-1][1]["brightness"] == 200
        and reading.calls[-1][1]["brightness"] == 80
    )
    kitchen._attr_brightness, reading._attr_brightness = 20, 250
    kitchen.async_write_ha_state()
    reading.async_write_ha_state()
    await seed(hass, robot)
    result, _ = await robot.command("make them brighter", route={"kind": "follow_up"})
    assert (
        result["outcome"] == "success"
        and kitchen.calls[-1][1]["brightness"] == 46
        and reading.calls[-1][1]["brightness"] == 255
    )
    kitchen._attr_brightness = 20
    kitchen.async_write_ha_state()
    await seed(hass, robot, "is the kitchen light on")
    result, _ = await robot.command("make it dimmer", route={"kind": "follow_up"})
    assert result["outcome"] == "success" and hass.states.get(kitchen.entity_id).state == "off"
    assert not bedroom.calls and entry.runtime_data.client.contexts


@pytest.mark.parametrize(
    "invalid", ["unsupported", "off", "unavailable", "unknown", "missing", True, math.nan, math.inf, -1, 256]
)
async def test_relative_entire_group_preflight_never_calls_on_invalid_member(hass, robot, invalid):
    entry, kitchen, reading, _ = await relative_group(hass, robot)
    if invalid == "unsupported":
        reading._attr_supported_color_modes, reading._attr_color_mode = {ColorMode.ONOFF}, ColorMode.ONOFF
        reading.async_write_ha_state()
    elif invalid == "off":
        reading._attr_is_on = False
        reading.async_write_ha_state()
    elif invalid == "missing":
        hass.states.async_remove(reading.entity_id)
    elif invalid in ("unavailable", "unknown"):
        hass.states.async_set(reading.entity_id, invalid)
    else:
        reading._attr_brightness = invalid
        reading.async_write_ha_state()
    result, _ = await robot.command("make them dimmer", route={"kind": "follow_up"})
    assert result["outcome"] == "error", result
    assert not kitchen.calls and not reading.calls and not entry.runtime_data.client.contexts


@pytest.mark.parametrize("change", ["exposure", "off", "brightness"])
async def test_relative_pending_target_race_stops_without_retry(hass, robot, monkeypatch, change):
    entry, kitchen, reading, _ = await relative_group(hass, robot)
    original = kitchen.async_turn_on

    async def changed(**kwargs):
        await original(**kwargs)
        if change == "exposure":
            exposed_entities.async_expose_entity(hass, "conversation", reading.entity_id, False)
        else:
            if change == "off":
                reading._attr_is_on = False
            else:
                reading._attr_brightness = 70
            reading.async_write_ha_state()

    monkeypatch.setattr(kitchen, "async_turn_on", changed)
    result, _ = await robot.command("make them dimmer", route={"kind": "follow_up"})
    assert result["outcome"] in ("partial", "uncertain"), result
    assert len(kitchen.calls) == 1 and not reading.calls and not entry.runtime_data.client.contexts


async def test_relative_unconfirmed_brightness_uses_original_deadline(hass, robot, monkeypatch):
    kitchen, _ = await devices(hass)
    kitchen._attr_is_on, kitchen._attr_brightness = True, 200
    kitchen.async_write_ha_state()
    entry = await linked_entry(hass, robot)
    await seed(hass, robot)

    async def lost(**kwargs):
        kitchen.calls.append(("on", kwargs))

    monkeypatch.setattr(kitchen, "async_turn_on", lost)
    result, latency = await robot.command("make it dimmer", route={"kind": "follow_up"}, deadline_seconds=2)
    assert result["outcome"] == "uncertain" and 1900 <= latency <= 2800, result
    assert len(kitchen.calls) == 1 and not entry.runtime_data.client.contexts


async def test_missing_relative_context_never_calls_selected_agent(hass, robot):
    kitchen, _ = await devices(hass)
    agent = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await linked_entry(hass, robot)
    await choose_agent(hass, entry, agent.entity_id)
    await wait_for(lambda: entry.runtime_data.client.ready)
    result, _ = await robot.command("make it dimmer", route={"kind": "follow_up"})
    assert result["code"] == "no_context" and not agent.calls and not kitchen.calls


async def test_local_notify_opt_in_quiet_hours_and_confirmation(hass, robot):
    await devices(hass)
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    status = er.async_get(hass).async_get_entity_id("sensor", "phoenix", f"{robot.robot_id}_announcement_status")
    notify = er.async_get(hass).async_get_entity_id("notify", "phoenix", f"{robot.robot_id}_announcement")
    assert hass.states.get(status).state == "permission_required" and hass.states.get(notify).state == "unavailable"
    assert hass.states.get(status).attributes["minimum_firmware"] == "13.2.0"
    with pytest.raises(ServiceValidationError) as error:
        await client.async_announce(robot.robot_id, "Invented message")
    assert error.value.translation_key == "permission_denied" and not robot.speech_calls
    client = await allow_announcements(hass, entry, robot)
    await wait_for(lambda: hass.states.get(status).state == "ready")
    await hass.services.async_call(
        "notify", "send_message", {"message": "Invented message"}, target={"entity_id": notify}, blocking=True
    )
    assert len(robot.speech_calls) == 1 and "volume" not in robot.speech_calls[0]
    robot.action_outcome = "uncertain"
    with pytest.raises(HomeAssistantError) as error:
        await client.async_announce(robot.robot_id, "Invented uncertain message")
    assert error.value.translation_key == "confirmation_lost" and len(robot.speech_calls) == 2
    old = client
    hass.config_entries.async_update_entry(
        entry,
        options={
            "allow_announcements": True,
            "quiet_hours_enabled": True,
            "quiet_hours_start": "00:00:00",
            "quiet_hours_end": "00:00:00",
        },
    )
    await wait_for(
        lambda: entry.runtime_data.client is not old and entry.runtime_data.client.ready and robot.announcements_enabled
    )
    with pytest.raises(ServiceValidationError) as error:
        await entry.runtime_data.client.async_announce(robot.robot_id, "Invented quiet message")
    assert error.value.translation_key == "quiet_hours" and len(robot.speech_calls) == 2
    await wait_for(lambda: hass.states.get(status).state == "quiet_hours")


async def test_reverse_pending_blocks_commands_disconnect_is_uncertain_no_replay(hass, robot):
    kitchen, _ = await devices(hass)
    entry = await linked_entry(hass, robot)
    client = await allow_announcements(hass, entry, robot)
    robot.action_outcome = "hold"
    task = asyncio.create_task(client.async_announce(robot.robot_id, "Invented held message"))
    await wait_for(lambda: robot.speech_calls)
    result, _ = await robot.command("turn on kitchen light")
    assert result["code"] == "busy" and not kitchen.calls
    await robot.disconnect()
    with pytest.raises(HomeAssistantError) as error:
        await task
    assert error.value.translation_key == "confirmation_lost"
    await wait_for(lambda: client.ready)
    assert len(robot.speech_calls) == 1 and not client.pending_actions and not kitchen.calls


async def test_announcement_status_local_clock_transition_and_timer_cleanup(hass, robot):
    entry = await linked_entry(hass, robot)
    now = dt_util.utcnow()
    old = entry.runtime_data.client
    hass.config_entries.async_update_entry(
        entry,
        options={
            "allow_announcements": True,
            "quiet_hours_enabled": True,
            "quiet_hours_start": (now + timedelta(seconds=4)).strftime("%H:%M:%S"),
            "quiet_hours_end": (now + timedelta(seconds=90)).strftime("%H:%M:%S"),
        },
    )
    await wait_for(
        lambda: entry.runtime_data.client is not old and entry.runtime_data.client.ready and robot.announcements_enabled
    )
    client = entry.runtime_data.client
    status = er.async_get(hass).async_get_entity_id("sensor", "phoenix", f"{robot.robot_id}_announcement_status")
    await wait_for(lambda: hass.states.get(status).state == "ready")
    tracks = []
    for handle in tuple(hass.loop._scheduled):
        track = getattr(handle._callback, "__self__", None)
        entity = getattr(getattr(track, "action", None), "__self__", None)
        if (
            entity is not None
            and getattr(entity, "translation_key", None) == "announcement_status"
            and entity.client is client
        ):
            tracks.append(track)
    assert len(tracks) == 1 and tracks[0].seconds == 30
    roster_count = sum(frame["type"] == "roster" for frame in robot.sent)
    await wait_for(lambda: hass.states.get(status).state == "quiet_hours", seconds=32)
    assert sum(frame["type"] == "roster" for frame in robot.sent) == roster_count and not robot.speech_calls
    await hass.config_entries.async_unload(entry.entry_id)
    assert tracks[0]._timer_handle.cancelled()
    assert not any(
        not handle.cancelled() and getattr(handle._callback, "__self__", None) is tracks[0]
        for handle in tuple(hass.loop._scheduled)
    )


@pytest.mark.parametrize(
    "flag,status,error_code",
    [
        ("supported", "firmware_required", "unsupported_robot_announcements"),
        ("online", "offline", "robot_offline"),
        ("busy", "busy", "robot_busy"),
    ],
)
async def test_status_and_local_announcement_preflight_match_without_outgoing_actions(
    hass, robot, flag, status, error_code
):
    entry = await linked_entry(hass, robot)
    client = await allow_announcements(hass, entry, robot)
    setattr(robot, flag, flag == "busy")
    await robot.roster()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "phoenix", f"{robot.robot_id}_announcement_status")
    await wait_for(lambda: hass.states.get(entity_id).state == status)
    assert hass.states.get(entity_id).attributes["minimum_firmware"] == "13.2.0"
    with pytest.raises(ServiceValidationError) as error:
        await client.async_announce(robot.robot_id, "Invented blocked announcement")
    assert error.value.translation_key == error_code and not robot.speech_calls
    assert not any(frame["type"] == "robot_action" for frame in robot.frames)


async def test_roster_busy_change_during_disk_admission_prevents_reverse_send(hass, robot, monkeypatch):
    entry = await linked_entry(hass, robot)
    client = await allow_announcements(hass, entry, robot)
    waiting, proceed = asyncio.Event(), asyncio.Event()
    original = client.storage.async_save

    async def slow_save(data):
        waiting.set()
        await proceed.wait()
        await original(data)

    monkeypatch.setattr(client.storage, "async_save", slow_save)
    task = asyncio.create_task(client.async_announce(robot.robot_id, "Invented busy race"))
    await waiting.wait()
    robot.busy = True
    await robot.roster()
    await wait_for(lambda: client.robots[robot.robot_id].busy)
    proceed.set()
    with pytest.raises(ServiceValidationError) as error:
        await task
    assert error.value.translation_key == "robot_busy" and not robot.speech_calls
    assert not any(frame["type"] == "robot_action" for frame in robot.frames)
