"""Importable blueprints run in the actual HA engine against invented TLS peers."""

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.homeassistant.triggers import time as time_trigger
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util import yaml as yaml_util

from tests.control_backend import SyntheticControlRobot
from tests.direct_backend import linked_entry, wait_for
from tests.test_controls import enable_controls, entity
from tests.test_transport import allow_announcements

BLUEPRINTS = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "phoenix"


def announcement_entity(hass, robot):
    entity_id = er.async_get(hass).async_get_entity_id("notify", "phoenix", f"{robot.robot_id}_announcement")
    assert entity_id
    return entity_id


async def install_automation(hass, name, inputs):
    """Load the file through the same use_blueprint path an owner imports."""
    destination = Path(hass.config.path("blueprints", "automation", "phoenix"))
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BLUEPRINTS / f"{name}.yaml", destination / f"{name}.yaml")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "invented_blueprint",
                    "alias": "Invented blueprint",
                    "use_blueprint": {"path": f"phoenix/{name}.yaml", "input": inputs},
                }
            ]
        },
    )
    await hass.async_start()
    await asyncio.sleep(0.05)
    assert hass.states.get("automation.invented_blueprint").state == "on"


async def set_states(hass, entity_id, *states):
    for state in states:
        hass.states.async_set(entity_id, state)
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("path", sorted(BLUEPRINTS.glob("*.yaml")), ids=lambda path: path.stem)
def test_importable_blueprint_metadata_selectors_and_defaults(path):
    data = yaml_util.load_yaml(path)
    blueprint = Blueprint(data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert blueprint.validate() is None
    assert blueprint.metadata["source_url"].endswith(f"/blueprints/automation/phoenix/{path.name}")
    required = {
        name: f"{'notify' if name == 'announcement_entity' else 'switch' if name == 'sleep_entity' else 'binary_sensor' if name == 'plugged_in_entity' else 'sensor'}.invented"
        for name, config in blueprint.inputs.items()
        if "default" not in config
    }
    instance = BlueprintInputs(blueprint, {"use_blueprint": {"path": path.name, "input": required}})
    instance.validate()
    assert "blueprint" not in instance.async_substitute()


async def test_state_announcement_exact_transition_and_cooldown(hass, robot):
    entry = await linked_entry(hass, robot)
    await allow_announcements(hass, entry, robot)
    await install_automation(
        hass,
        "state_announcement",
        {
            "watched_entity": "binary_sensor.invented_door",
            "announcement_entity": announcement_entity(hass, robot),
            "message": "  The invented door is open.  ",
        },
    )
    await set_states(hass, "binary_sensor.invented_door", "on", "unavailable", "on")
    assert robot.speech_calls == []
    await set_states(hass, "binary_sensor.invented_door", "off", "on")
    await wait_for(lambda: len(robot.speech_calls) == 1)
    assert robot.speech_calls[0]["text"] == "The invented door is open."
    await set_states(hass, "binary_sensor.invented_door", "off", "on", "off", "on")
    assert len(robot.speech_calls) == 1


async def test_state_announcement_wait_cancels_when_state_changes(hass, robot):
    entry = await linked_entry(hass, robot)
    await allow_announcements(hass, entry, robot)
    await install_automation(
        hass,
        "state_announcement",
        {
            "watched_entity": "binary_sensor.invented_door",
            "announcement_entity": announcement_entity(hass, robot),
            "hold_seconds": 1,
        },
    )
    await set_states(hass, "binary_sensor.invented_door", "off", "on", "off")
    await asyncio.sleep(1.1)
    assert not robot.speech_calls
    await set_states(hass, "binary_sensor.invented_door", "on")
    await wait_for(lambda: len(robot.speech_calls) == 1, seconds=3)


@pytest.mark.parametrize("message", [" ", "x" * 301])
async def test_state_announcement_rejects_empty_or_oversized_message(hass, robot, message):
    entry = await linked_entry(hass, robot)
    await allow_announcements(hass, entry, robot)
    await install_automation(
        hass,
        "state_announcement",
        {
            "watched_entity": "binary_sensor.invented_door",
            "announcement_entity": announcement_entity(hass, robot),
            "message": message,
        },
    )
    await set_states(hass, "binary_sensor.invented_door", "off", "on")
    assert not robot.speech_calls


@pytest.mark.parametrize("blocked", ["permission", "busy", "quiet_hours", "offline"])
async def test_state_announcement_never_replays_after_blocked_trigger(hass, robot, blocked):
    entry = await linked_entry(hass, robot)
    client = await allow_announcements(hass, entry, robot)
    if blocked == "permission":
        robot.announcements_enabled = False
    elif blocked == "busy":
        robot.busy = True
    elif blocked == "offline":
        robot.online = False
    else:
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                "quiet_hours_enabled": True,
                "quiet_hours_start": "00:00",
                "quiet_hours_end": "00:00",
            },
        )
        await wait_for(lambda: entry.runtime_data.client is not client and entry.runtime_data.client.ready)
        client = entry.runtime_data.client
    await robot.roster()
    await asyncio.sleep(0.05)
    await install_automation(
        hass,
        "state_announcement",
        {
            "watched_entity": "binary_sensor.invented_door",
            "announcement_entity": announcement_entity(hass, robot),
        },
    )
    await set_states(hass, "binary_sensor.invented_door", "off", "on")
    assert not robot.speech_calls
    robot.announcements_enabled = True
    robot.busy = False
    robot.online = True
    if blocked == "quiet_hours":
        hass.config_entries.async_update_entry(entry, options={**entry.options, "quiet_hours_enabled": False})
        await wait_for(lambda: entry.runtime_data.client is not client and entry.runtime_data.client.ready)
    await robot.roster()
    await asyncio.sleep(0.05)
    assert not robot.speech_calls


async def battery_inputs(hass, robot):
    entry = await linked_entry(hass, robot)
    await allow_announcements(hass, entry, robot)
    await robot.telemetry({"battery_percent": 50, "plugged_in": False})
    await wait_for(lambda: entry.runtime_data.client.telemetry_available("battery_percent"))
    await asyncio.sleep(0.05)
    registry = er.async_get(hass)
    return {
        "battery_entity": registry.async_get_entity_id("sensor", "phoenix", f"{robot.robot_id}_battery_percent"),
        "plugged_in_entity": registry.async_get_entity_id("binary_sensor", "phoenix", f"{robot.robot_id}_plugged_in"),
        "announcement_entity": announcement_entity(hass, robot),
    }


async def test_battery_reminder_actual_telemetry_crossing_and_cooldown(hass, robot):
    inputs = await battery_inputs(hass, robot)
    await install_automation(hass, "battery_reminder", inputs)
    await robot.telemetry({"battery_percent": 19, "plugged_in": False})
    await wait_for(lambda: len(robot.speech_calls) == 1)
    assert robot.speech_calls[0]["text"] == "My battery is getting low. Please plug me in."
    await robot.telemetry({"battery_percent": 18, "plugged_in": True})
    await asyncio.sleep(0.05)
    await robot.telemetry({"battery_percent": 17, "plugged_in": False})
    await asyncio.sleep(0.05)
    assert len(robot.speech_calls) == 1


async def test_battery_reminder_does_not_guess_from_missing_or_reconnected_reading(hass, robot):
    inputs = await battery_inputs(hass, robot)
    await install_automation(hass, "battery_reminder", inputs)
    await set_states(hass, inputs["battery_entity"], "unavailable", "10", "unknown", "9")
    assert not robot.speech_calls
    await set_states(hass, inputs["plugged_in_entity"], "unavailable", "off")
    assert not robot.speech_calls
    await set_states(hass, inputs["battery_entity"], "unknown")
    await set_states(hass, inputs["plugged_in_entity"], "on", "off")
    assert not robot.speech_calls
    await set_states(hass, inputs["battery_entity"], "8")
    assert not robot.speech_calls
    await set_states(hass, inputs["plugged_in_entity"], "on", "off")
    await wait_for(lambda: len(robot.speech_calls) == 1)


async def test_battery_reminder_stays_quiet_when_plugged_in(hass, robot):
    inputs = await battery_inputs(hass, robot)
    await install_automation(hass, "battery_reminder", inputs)
    await set_states(hass, inputs["plugged_in_entity"], "on")
    await set_states(hass, inputs["battery_entity"], "19", "18")
    assert not robot.speech_calls


@pytest.fixture
async def scheduled_robot(tmp_path):
    peer = await SyntheticControlRobot(tmp_path / "invented-scheduled-robot").start()
    try:
        yield peer
    finally:
        await peer.close()


@pytest.fixture
def scheduled_events(monkeypatch):
    """Exercise HA's actual time-trigger callbacks with controlled clock events."""
    events = {}

    def subscribe(hass, action, hour=None, minute=None, second=None, **kwargs):
        key = f"{hour:02}:{minute:02}:{second:02}"
        events.setdefault(key, []).append(action)
        return lambda: events[key].remove(action)

    monkeypatch.setattr(time_trigger, "async_track_time_change", subscribe)

    def fire(value):
        for listener in list(events.get(value, [])):
            listener(dt_util.now())

    return fire


async def test_sleep_schedule_uses_real_native_services_and_does_not_repeat(hass, scheduled_robot, scheduled_events):
    entry = await linked_entry(hass, scheduled_robot)
    await enable_controls(hass, entry, scheduled_robot, ["sleep"])
    switch = entity(hass, entry, "switch", "sleep_control")
    await install_automation(hass, "sleep_schedule", {"sleep_entity": switch.entity_id})
    assert not scheduled_robot.control_calls
    scheduled_events("22:00:00")
    await wait_for(lambda: switch.is_on is True)
    assert [frame["action"] for frame in scheduled_robot.control_calls] == ["sleep"]
    scheduled_events("22:00:00")
    await asyncio.sleep(0.05)
    assert len(scheduled_robot.control_calls) == 1
    scheduled_events("08:00:00")
    await wait_for(lambda: switch.is_on is False)
    assert [frame["action"] for frame in scheduled_robot.control_calls] == ["sleep", "wake"]


@pytest.mark.parametrize("invalid", ["same_time", "same_time_short", "other_day", "unavailable", "permission"])
async def test_sleep_schedule_skips_invalid_or_unavailable_events(
    hass, scheduled_robot, scheduled_events, monkeypatch, invalid
):
    entry = await linked_entry(hass, scheduled_robot)
    if invalid != "permission":
        await enable_controls(hass, entry, scheduled_robot, ["sleep"])
    switch = entity(hass, entry, "switch", "sleep_control")
    inputs = {"sleep_entity": switch.entity_id}
    if invalid == "same_time":
        inputs["wake_time"] = "22:00:00"
    elif invalid == "same_time_short":
        inputs["wake_time"] = "22:00"
    elif invalid == "other_day":
        monkeypatch.setattr(dt_util, "now", lambda *args: datetime(2026, 10, 7, 22, tzinfo=timezone.utc))
        inputs["weekdays"] = ["mon"]
    await install_automation(hass, "sleep_schedule", inputs)
    if invalid == "unavailable":
        await set_states(hass, switch.entity_id, "unavailable")
    scheduled_events("22:00:00")
    await asyncio.sleep(0.05)
    assert not scheduled_robot.control_calls
    if invalid == "unavailable":
        switch.async_write_ha_state()
        await asyncio.sleep(0.05)
        assert not scheduled_robot.control_calls
