"""Actual HA telemetry units, registry roles, freshness and unavailable values."""

import asyncio
import math

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.phoenix import diagnostics
from tests.direct_backend import linked_entry, wait_for

VALUES = {
    "battery_percent": 73,
    "battery_temperature_c": 25,
    "camera": "idle",
    "charging_state": "not_charging",
    "cpu_temperature_c": 42,
    "fan_percent": 25,
    "hatch_open": False,
    "head_touch": False,
    "main_board_temperature_c": 35,
    "microphone_rms_db": -32.5,
    "plugged_in": True,
    "sleeping": False,
    "speaker_volume_percent": 61,
    "system_voltage_v": 12.25,
}
BINARY_KEYS = {"hatch_open", "head_touch", "plugged_in", "sleeping", "robot_online"}


def metric_id(hass, robot, key):
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor" if key in BINARY_KEYS else "sensor", "phoenix", f"{robot.robot_id}_{key}"
    )
    assert entity_id, key
    return entity_id


async def test_all_fifteen_readings_are_registered_on_the_paired_device(hass, robot):
    entry = await linked_entry(hass, robot)
    await robot.telemetry(VALUES)
    await wait_for(lambda: entry.runtime_data.client.telemetry_available("battery_percent"))
    await hass.async_block_till_done()
    device_id = entry.runtime_data.client.robots[robot.robot_id].device_id
    expected = {**VALUES, "robot_online": True}
    for key, value in expected.items():
        entity_id = metric_id(hass, robot, key)
        registered = er.async_get(hass).async_get(entity_id)
        assert registered.device_id == device_id
        state = hass.states.get(entity_id)
        if key in BINARY_KEYS:
            assert state.state == ("on" if value else "off"), (key, state)
        elif isinstance(value, (int, float)):
            assert float(state.state) == value, (key, state)
            assert state.attributes["state_class"] == "measurement"
        else:
            assert state.state == value
    assert hass.states.get(metric_id(hass, robot, "hatch_open")).attributes["device_class"] == "opening"
    assert hass.states.get(metric_id(hass, robot, "plugged_in")).attributes["device_class"] == "plug"
    assert hass.states.get(metric_id(hass, robot, "battery_percent")).attributes["device_class"] == "battery"
    assert hass.states.get(metric_id(hass, robot, "camera")).attributes["options"] == ["idle", "active", "disabled"]
    assert hass.states.get(metric_id(hass, robot, "microphone_rms_db")).attributes["unit_of_measurement"] == "dB"
    assert hass.states.get(metric_id(hass, robot, "system_voltage_v")).attributes["unit_of_measurement"] == "V"
    exported = str(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
    assert "microphone_rms_db" not in exported and "12.25" not in exported and robot.robot_id not in exported
    assert robot.speech_calls == [] and not any(frame["type"] == "robot_action" for frame in robot.frames)


async def test_native_celsius_uses_actual_ha_fahrenheit_display(hass, robot):
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = await linked_entry(hass, robot)
    await robot.telemetry(VALUES)
    await wait_for(lambda: entry.runtime_data.client.telemetry_available("battery_temperature_c"))
    await hass.async_block_till_done()
    for key, expected in [
        ("battery_temperature_c", 77),
        ("cpu_temperature_c", 107.6),
        ("main_board_temperature_c", 95),
    ]:
        state = hass.states.get(metric_id(hass, robot, key))
        assert float(state.state) == pytest.approx(expected)
        assert state.attributes["unit_of_measurement"] == "°F" and state.attributes["device_class"] == "temperature"


async def test_missing_and_invalid_values_never_default_to_idle_or_off(hass, robot):
    entry = await linked_entry(hass, robot)
    malformed = {
        "battery_percent": True,
        "battery_temperature_c": math.nan,
        "camera": "recording",
        "charging_state": "invented",
        "head_touch": 0,
        "hatch_open": "closed",
        "system_voltage_v": 101,
        "microphone_rms_db": math.inf,
        "speaker_volume_percent": -1,
        "fan_percent": 34,
    }
    await robot.telemetry(malformed)
    await wait_for(lambda: entry.runtime_data.client.telemetry_available("fan_percent"))
    await hass.async_block_till_done()
    for key in VALUES:
        if key == "fan_percent":
            assert hass.states.get(metric_id(hass, robot, key)).state == "34"
        else:
            assert hass.states.get(metric_id(hass, robot, key)).state == "unavailable", key
    assert hass.states.get(metric_id(hass, robot, "robot_online")).state == "on"


async def test_measurement_age_expires_without_cached_heartbeat_refresh(hass, robot):
    entry = await linked_entry(hass, robot)
    observed = robot.now_ms() - 28_800
    await robot.telemetry({"battery_percent": 42}, observed_at_ms=observed)
    await wait_for(lambda: entry.runtime_data.client.telemetry_available("battery_percent"))
    await asyncio.sleep(0.5)
    await robot.telemetry({"battery_percent": 42}, observed_at_ms=observed)
    await wait_for(lambda: not entry.runtime_data.client.telemetry_available("battery_percent"), seconds=2)
    await hass.async_block_till_done()
    assert hass.states.get(metric_id(hass, robot, "battery_percent")).state == "unavailable"
    assert entry.runtime_data.client.ready and hass.states.get(metric_id(hass, robot, "robot_online")).state == "on"


async def test_touch_edges_publish_promptly_and_disconnect_clears_values(hass, robot):
    entry = await linked_entry(hass, robot)
    await robot.telemetry({**VALUES, "head_touch": True})
    touch = metric_id(hass, robot, "head_touch")
    await wait_for(lambda: hass.states.get(touch).state == "on")
    await robot.telemetry({**VALUES, "head_touch": False})
    await wait_for(lambda: hass.states.get(touch).state == "off")
    await robot.disconnect()
    await wait_for(lambda: not entry.runtime_data.client.ready)
    assert entry.runtime_data.client._telemetry_timer is None and entry.runtime_data.client.telemetry_values == {}
    await hass.async_block_till_done()
    assert hass.states.get(touch).state == "unavailable"
    assert hass.states.get(metric_id(hass, robot, "robot_online")).state == "off"


@pytest.mark.parametrize("age", [-2000, 31_000])
async def test_future_and_stale_measurements_are_unavailable(hass, robot, age):
    entry = await linked_entry(hass, robot)
    await robot.telemetry(VALUES, age_ms=age)
    await asyncio.sleep(0.05)
    assert entry.runtime_data.client.telemetry_values == {}
    assert not entry.runtime_data.client.telemetry_available("battery_percent")


async def test_unload_cancels_expiry_and_reconnect_requires_new_observation(hass, robot):
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    await robot.telemetry(VALUES)
    await wait_for(lambda: client._telemetry_timer is not None)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client._telemetry_timer is None and client.telemetry_values == {}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.ready)
    await hass.async_block_till_done()
    assert hass.states.get(metric_id(hass, robot, "battery_percent")).state == "unavailable"
    assert hass.states.get(metric_id(hass, robot, "robot_online")).state == "on"
