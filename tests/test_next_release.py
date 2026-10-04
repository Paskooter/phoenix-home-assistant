"""Independent release checks over actual HA and Phoenix; every fixture is invented.

The classifier network and the physical robot are synthetic. The installed HA
APIs, config flows, TLS socket, Account authorizer and Gateway are real.
This suite exercises a trusted Phoenix server and makes no operator-exclusion
or end-to-end content-encryption claim.
"""

import asyncio
import json
import os
import ssl
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import aiohttp
import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.phoenix import diagnostics
from tests.test_conversation import SyntheticLight, install_devices
from tests.test_transport import linked_entry, wait_for


@pytest.fixture
async def next_backend(tmp_path):
    """Use isolated ports/storage and never contact an owner's server."""
    source = os.environ.get("PHOENIX_SERVER_DIR")
    if not source:
        pytest.skip("Set PHOENIX_SERVER_DIR for the cross-repository release checks")
    process = await asyncio.create_subprocess_exec(
        "node",
        str(Path(__file__).with_name("next_release_backend.mjs")),
        cwd=source,
        env={**os.environ, "PHOENIX_HA_TEST_ROOT": str(tmp_path)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    errors = asyncio.create_task(process.stderr.read())
    remaining_output = None
    try:
        async with asyncio.timeout(25):
            while line := await process.stdout.readline():
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if "url" in data and "robots" in data:
                    break
            else:
                raise AssertionError((await errors).decode())
        remaining_output = asyncio.create_task(process.stdout.read())
        context = ssl.create_default_context(cafile=data["certificate"])
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context)) as session:
            yield data, session
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        stderr = await errors
        if stderr:
            print("Synthetic backend stderr:\n" + stderr.decode())
        if remaining_output is not None:
            stdout = await remaining_output
            if stdout:
                print("Synthetic backend output:\n" + stdout.decode())


async def snapshot(backend):
    data, session = backend
    async with session.get(data["url"] + "/test/state") as response:
        assert response.status == 200
        return await response.json()


async def control(backend, endpoint, payload=None):
    data, session = backend
    async with session.post(data["url"] + endpoint, json=payload or {}) as response:
        assert response.status == 200, await response.text()
        return await response.json()


async def wait_until(predicate, seconds=8, *, interval=0.02):
    async with asyncio.timeout(seconds):
        while not await predicate():
            await asyncio.sleep(interval)


async def next_entry(hass, backend, monkeypatch):
    entry = await linked_entry(hass, backend, monkeypatch)
    await wait_for(lambda: len(entry.runtime_data.client.robots) == 2)
    return entry


def robot_context(entry, index=0):
    return list(entry.runtime_data.client.robots.values())[index]


async def options(hass, entry, **values):
    """Use the owner's real options flow and wait for its lifecycle reload."""
    old = entry.runtime_data.client
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], values)
    assert result["type"] == "create_entry", result.get("errors", result["type"])
    await wait_for(lambda: entry.runtime_data.client is not old and entry.runtime_data.client.state == "connected")
    await wait_for(lambda: len(entry.runtime_data.client.robots) == 2)
    return old


async def owner_permission(backend, entry, enabled, *, stranger=False):
    data, session = backend
    async with session.put(
        data["url"] + "/api/home-assistant/installation",
        headers={
            "Cookie": data["stranger_cookie" if stranger else "owner_cookie"],
            "X-Phoenix-API-Client": "synthetic-release-validation",
        },
        json={"installationId": entry.data["installation_id"], "announcementsEnabled": enabled},
    ) as response:
        if stranger:
            assert response.status in (403, 404)
        else:
            assert response.status == 200, await response.text()
    if not stranger:
        await wait_for(
            lambda: all(item.announcements_allowed is enabled for item in entry.runtime_data.client.robots.values())
        )


def announcement_role(hass, entry, robot_id, role="announcement_status"):
    robot = entry.runtime_data.client.robots[robot_id]
    rows = [
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.translation_key == role and row.device_id == robot.device_id
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row.unique_id == f"{entry.data['installation_id']}_{robot_id}_{role}"
    return row


async def announcement_status(hass, entry, robot_id, expected, *, notify_available, seconds=6):
    await wait_for(
        lambda: (
            len(
                [
                    row
                    for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                    if row.translation_key in {"announcement_status", "announcement"}
                    and row.device_id == entry.runtime_data.client.robots[robot_id].device_id
                ]
            )
            == 2
        ),
        seconds=seconds,
    )
    row = announcement_role(hass, entry, robot_id)
    native = announcement_role(hass, entry, robot_id, "announcement")
    await wait_for(
        lambda: (
            (state := hass.states.get(row.entity_id)) is not None
            and state.state == expected
            and (notify := hass.states.get(native.entity_id)) is not None
            and (notify.state != "unavailable") == notify_available
        ),
        seconds=seconds,
    )
    state = hass.states.get(row.entity_id)
    assert row.entity_category == EntityCategory.DIAGNOSTIC
    assert row.entity_id.startswith("sensor.")
    assert state.attributes["minimum_firmware"] == "13.1.1"
    assert state.attributes["device_class"] == "enum"
    assert expected in state.attributes["options"]
    if notify_available:
        assert hass.states.get(native.entity_id).attributes["minimum_firmware"] == "13.1.1"
    return state


async def announcement_timers(hass, entry):
    """Inspect real HA interval handles without invoking or replacing callbacks."""
    async with asyncio.timeout(8):
        while True:
            tracks = []
            for handle in tuple(hass.loop._scheduled):
                if handle.cancelled():
                    continue
                track = getattr(handle._callback, "__self__", None)
                entity = getattr(getattr(track, "action", None), "__self__", None)
                if (
                    entity is not None
                    and entity.__class__.__module__ == "custom_components.phoenix.sensor"
                    and entity.translation_key == "announcement_status"
                    and entity.client.entry.entry_id == entry.entry_id
                ):
                    assert entity.client is entry.runtime_data.client, "Old sensor interval survived reload"
                    assert track.seconds == 30
                    assert not track._timer_handle.cancelled()
                    tracks.append(track)
            assert len(tracks) <= 2, "Status intervals accumulated across reload"
            if len(tracks) == 2:
                return tracks
            await asyncio.sleep(0.02)


def assert_announcement_timers_removed(hass, tracks):
    for track in tracks:
        assert track._timer_handle.cancelled()
        assert not any(
            not handle.cancelled() and getattr(handle._callback, "__self__", None) is track
            for handle in tuple(hass.loop._scheduled)
        )


async def voice(backend, text, *, robot=0, hotphrase=True, skill=None, forged_context=False):
    data, session = backend
    metadata = data["robots"][robot]
    before = await snapshot(backend)
    previous_requests = {frame.get("request_id") for frame in before["server_frames"] if frame["type"] == "command"}
    start = time.perf_counter()
    async with session.ws_connect(
        data["gateway_url"].replace("http://", "ws://") + "/v1/listen",
        headers={"Authorization": f"Bearer {metadata['robot_token']}", "x-jibo-robotid": "forged-header-id"},
    ) as socket:
        await socket.send_json(
            {
                "type": "LISTEN",
                "data": {"lang": "en-US", "hotphrase": hotphrase, "rules": ["launch"], "mode": "CLIENT_ASR"},
            }
        )
        await socket.send_json(
            {
                "type": "CONTEXT",
                "data": {
                    "general": {
                        "accountID": "forged-household" if forged_context else metadata["identity"]["id"],
                        "robotID": "forged-robot" if forged_context else metadata["friendly_id"],
                        "release": "2.0.1",
                        "lang": "en",
                    },
                    "runtime": {},
                    "skill": {"id": skill} if skill else {},
                },
            }
        )
        await socket.send_json({"type": "CLIENT_ASR", "data": {"text": text, "confidence": 1}})
        frames = []
        async with asyncio.timeout(12):
            async for message in socket:
                frame = message.json()
                frames.append(frame)
                if frame.get("final"):
                    break
        assert frames and frames[-1].get("final"), "No final robot reply"
    milliseconds = (time.perf_counter() - start) * 1000
    after = await snapshot(backend)
    dispatched = [
        frame
        for frame in after["server_frames"]
        if frame["type"] == "command" and frame["request_id"] not in previous_requests
    ]
    result = [
        frame["result"]
        for frame in after["client_frames"]
        if frame["type"] == "result" and any(item["request_id"] == frame["request_id"] for item in dispatched)
    ]
    print(
        json.dumps(
            {
                "synthetic_tls_voice_ms": round(milliseconds, 1),
                "ha_version": HA_VERSION,
                "case": text,
                "route": dispatched[-1].get("route") if dispatched else None,
                "result": result[-1] if result else None,
            }
        )
    )
    return frames


def speech(frames):
    assert frames[-1]["type"] == "SKILL_ACTION", json.dumps(frames[-1])
    assert frames[-1]["data"]["skill"]["id"] == "phoenix-home-assistant"
    return frames[-1]["data"]["action"]["config"]["jcp"]["children"][0]["config"]["play"]["esml"]


class RecordingAgent(conversation.ConversationEntity):
    """Record the actual HA ConversationInput, without a cloud account."""

    _attr_name = "Invented release agent"
    _attr_unique_id = "invented-release-agent"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self):
        self.calls = []
        self.hold = None
        self.started = asyncio.Event()

    @property
    def supported_languages(self):
        return ["en"]

    async def async_process(self, user_input):
        self.calls.append(user_input)
        self.started.set()
        if self.hold is not None:
            await self.hold.wait()
        answer = intent.IntentResponse(language=user_input.language)
        answer.response_type = intent.IntentResponseType.QUERY_ANSWER
        answer.async_set_speech("Invented answer with <angle> & characters.")
        return conversation.ConversationResult(
            response=answer, conversation_id=user_input.conversation_id or str(uuid4())
        )


class KitchenTemperature(SensorEntity):
    """A real exposed sensor; no household devices or credentials exist."""

    _attr_should_poll = False
    _attr_name = "Kitchen Temperature"
    _attr_unique_id = "invented-release-temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_value = 21

    def __init__(self, name="Kitchen Temperature", value=21):
        self._attr_name = name
        self._attr_unique_id = "invented-" + name.lower().replace(" ", "-")
        self._attr_native_value = value


class SyntheticHSLight(SyntheticLight):
    """HA normalizes CSS green's RGB intensity into hue and saturation."""

    _attr_supported_color_modes = {ColorMode.HS}
    _attr_color_mode = ColorMode.HS
    _attr_hs_color = (0, 0)

    async def async_turn_on(self, **kwargs):
        self._attr_hs_color = kwargs.get("hs_color", self._attr_hs_color)
        await super().async_turn_on(**kwargs)


async def release_devices(hass):
    kitchen, bedroom = await install_devices(hass)
    bedroom_area = ar.async_get(hass).async_create("Bedroom")
    er.async_get(hass).async_update_entity(bedroom.entity_id, area_id=bedroom_area.id)
    assert await async_setup_component(hass, "sensor", {})
    temperature = KitchenTemperature()
    bedroom_temperature = KitchenTemperature("Bedroom Temperature", 18)
    await hass.data["sensor"].async_add_entities([temperature, bedroom_temperature])
    kitchen_area = ar.async_get(hass).async_get_area_by_name("Kitchen")
    er.async_get(hass).async_update_entity(temperature.entity_id, area_id=kitchen_area.id)
    exposed_entities.async_expose_entity(hass, "conversation", temperature.entity_id, True)
    er.async_get(hass).async_update_entity(bedroom_temperature.entity_id, area_id=bedroom_area.id)
    exposed_entities.async_expose_entity(hass, "conversation", bedroom_temperature.entity_id, True)
    return kitchen, bedroom, temperature


async def relative_devices(hass, *, invalid=None):
    kitchen, bedroom, _ = await release_devices(hass)
    reading = SyntheticLight("Kitchen Reading Light", "invented-relative-reading")
    reading._attr_is_on = True
    reading._attr_brightness = 80
    if invalid == "unsupported":
        reading._attr_supported_color_modes = {ColorMode.ONOFF}
        reading._attr_color_mode = ColorMode.ONOFF
    elif invalid == "off":
        reading._attr_is_on = False
    await hass.data["light"].async_add_entities([reading])
    area = ar.async_get(hass).async_get_area_by_name("Kitchen")
    er.async_get(hass).async_update_entity(reading.entity_id, area_id=area.id)
    exposed_entities.async_expose_entity(hass, "conversation", reading.entity_id, True)
    kitchen._attr_is_on = True
    kitchen._attr_brightness = 200
    kitchen.async_write_ha_state()
    return kitchen, reading, bedroom


def assign_room(hass, entry, area, *, robot=0):
    context = robot_context(entry, robot)
    room = ar.async_get(hass).async_get_area_by_name(area)
    dr.async_get(hass).async_update_device(context.device_id, area_id=room.id)


async def test_real_roster_room_context_and_wire_privacy(hass, next_backend, monkeypatch):
    await release_devices(hass)
    agent = RecordingAgent()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await next_entry(hass, next_backend, monkeypatch)
    assert all(not item.announcements_allowed for item in entry.runtime_data.client.robots.values())
    await options(hass, entry, conversation_agent=agent.entity_id)
    assign_room(hass, entry, "Kitchen", robot=0)
    assign_room(hass, entry, "Bedroom", robot=1)
    for index in range(2):
        frames = await voice(next_backend, "ask Home Assistant to say fixture greeting", robot=index)
        assert "&lt;angle&gt;" in speech(frames) and "&amp;" in speech(frames)
        assert agent.calls[-1].device_id == robot_context(entry, index).device_id
    assert agent.calls[0].device_id != agent.calls[1].device_id
    forged = await voice(next_backend, "ask Home Assistant to say fixture greeting", forged_context=True)
    assert forged[-1]["type"] == "ERROR" and len(agent.calls) == 2
    captured = await snapshot(next_backend)
    commands = [frame for frame in captured["server_frames"] if frame["type"] == "command"]
    assert len(commands) == 2
    assert all(frame["route"]["kind"] == "command" for frame in commands)
    assert all(frame["robot_id"] in entry.runtime_data.client.robots for frame in commands)
    assert all(frame["robot_id"] != row["identity"]["id"] for frame in commands for row in next_backend[0]["robots"])
    wire = json.dumps(captured["server_frames"] + captured["client_frames"])
    for private_value in [
        entry.data["credential"],
        "synthetic-next-owner",
        "synthetic-next-stranger",
        "forged-household",
    ]:
        assert private_value not in wire
    prefs = [frame for frame in captured["client_frames"] if frame["type"] == "preferences"]
    assert prefs
    assert all("entity_id" not in json.dumps(frame) and "conversation_id" not in frame for frame in prefs)


async def test_room_commands_and_local_queries_bypass_selected_cloud_agent(hass, next_backend, monkeypatch):
    kitchen, bedroom, temperature = await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    assert "couldn't" not in speech(await voice(next_backend, "turn on the lights here"))
    assert len(kitchen.calls) == 1 and bedroom.calls == []
    agent = RecordingAgent()
    await hass.data["conversation"].async_add_entities([agent])
    await options(hass, entry, conversation_agent=agent.entity_id)
    before = list(kitchen.calls)
    assert "on" in speech(await voice(next_backend, "are the kitchen lights on")).lower()
    assert "on" in speech(await voice(next_backend, "are the lights here on")).lower()
    assert "on" in speech(await voice(next_backend, "are the lights this room on")).lower()
    assert "off" in speech(await voice(next_backend, "and in the bedroom")).lower()
    assert "21" in speech(await voice(next_backend, "what is the kitchen temperature"))
    assert "18" in speech(await voice(next_backend, "and in the bedroom"))
    assert agent.calls == [] and kitchen.calls == before and bedroom.calls == []
    exposed_entities.async_expose_entity(hass, "conversation", temperature.entity_id, False)
    assert "21" not in speech(await voice(next_backend, "what is the kitchen temperature"))
    assert agent.calls == [] and kitchen.calls == before


async def test_unassigned_robot_generic_lights_never_expand_to_all_rooms(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    device = dr.async_get(hass).async_get(robot_context(entry).device_id)
    assert device is not None and device.area_id is None
    await voice(next_backend, "turn on the lights")
    results = [
        frame["result"] for frame in (await snapshot(next_backend))["client_frames"] if frame["type"] == "result"
    ]
    assert results and results[-1]["outcome"] == "error" and results[-1]["code"] == "room_not_configured"
    selected = RecordingAgent()
    await hass.data["conversation"].async_add_entities([selected])
    await options(hass, entry, conversation_agent=selected.entity_id)
    await voice(next_backend, "turn on the lights")
    assert selected.calls == [], "Missing room scope must be rejected before any selected agent"
    assert kitchen.calls == [] and bedroom.calls == []
    assert hass.states.get(kitchen.entity_id).state == hass.states.get(bedroom.entity_id).state == "off"
    assert not entry.runtime_data.client.contexts
    await voice(next_backend, "turn on the kitchen light")
    assert len(selected.calls) == 1, "An explicit target remains delegated without an assigned room"


async def test_owner_exact_shortcuts_require_live_assist_exposure(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    agent = RecordingAgent()
    await hass.data["conversation"].async_add_entities([agent])
    await options(
        hass,
        entry,
        conversation_agent=agent.entity_id,
        shortcut_phrase="reading time",
        shortcut_target="scene.dinner",
    )
    await voice(next_backend, "are kitchen lights on")
    assert entry.runtime_data.client.contexts
    assert "couldn't" not in speech(await voice(next_backend, "reading time"))
    assert len(kitchen.calls) == 1 and bedroom.calls == [] and agent.calls == []
    assert entry.runtime_data.client.contexts == {}
    await voice(next_backend, "reading times")
    assert len(kitchen.calls) == 1 and agent.calls == []
    exposed_entities.async_expose_entity(hass, "conversation", "scene.dinner", False)
    await voice(next_backend, "reading time")
    assert len(kitchen.calls) == 1 and agent.calls == []
    await options(
        hass,
        entry,
        conversation_agent=agent.entity_id,
        shortcut_phrase="dance",
        shortcut_target="script.relax",
    )
    commands_before = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    await voice(next_backend, "dance")
    commands_after = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    assert commands_after == commands_before, "A parser-recognized native command must retain its route"
    assert len(kitchen.calls) == 1 and bedroom.calls == [] and agent.calls == []
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"conversation_agent": agent.entity_id, "shortcut_phrase": "sleep", "shortcut_target": "script.relax"},
    )
    assert result["type"] == "form" and result["errors"], result
    hass.config_entries.options.async_abort(flow["flow_id"])


@pytest.mark.parametrize(
    ("mode", "followup"),
    [
        (ColorMode.BRIGHTNESS, "set it to blue"),
        (ColorMode.ONOFF, "set it brightness to 25 percent"),
        (ColorMode.HS, "set it to warm white"),
        (ColorMode.COLOR_TEMP, "set it to cool white"),
    ],
)
async def test_cached_followup_unsupported_light_features_never_execute(
    hass, next_backend, monkeypatch, mode, followup
):
    kitchen, bedroom, _ = await release_devices(hass)
    limited = SyntheticLight("Kitchen Limited Light", "invented-limited-" + mode.value)
    limited._attr_supported_color_modes = {mode}
    limited._attr_color_mode = mode
    if mode == ColorMode.COLOR_TEMP:
        limited._attr_min_color_temp_kelvin = 2700
        limited._attr_max_color_temp_kelvin = 4000
        limited._attr_color_temp_kelvin = 3000
    await hass.data["light"].async_add_entities([limited])
    area = ar.async_get(hass).async_get_area_by_name("Kitchen")
    er.async_get(hass).async_update_entity(limited.entity_id, area_id=area.id)
    exposed_entities.async_expose_entity(hass, "conversation", limited.entity_id, True)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    await voice(next_backend, "is the kitchen limited light on")
    context = entry.runtime_data.client.contexts[robot_context(entry).robot_id]
    assert context.targets == (limited.entity_id,)
    await voice(next_backend, followup)
    results = [
        frame["result"] for frame in (await snapshot(next_backend))["client_frames"] if frame["type"] == "result"
    ]
    assert results[-1]["outcome"] == "error" and results[-1]["code"] == "unsupported_feature"
    assert hass.states.get(limited.entity_id).state == "off"
    assert limited.calls == [] and kitchen.calls == [] and bedroom.calls == []
    assert not entry.runtime_data.client.contexts


async def test_invalid_observed_color_state_never_confirms_success(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    for attributes in [{"hs_color": (float("nan"), 100)}, {"rgb_color": (0, float("inf"), 0)}]:
        # A provider's invalid observed values cannot prove the requested color.
        # This changes only the invented HA state, without calling a service.
        hass.states.async_set(kitchen.entity_id, "on", attributes)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.12):
                await entry.runtime_data.client._confirm_light_settings(
                    (kitchen.entity_id,), {"rgb_color": (0, 128, 0)}
                )
    for brightness in [None, True, float("nan"), float("inf"), -1, 256]:
        hass.states.async_set(kitchen.entity_id, "on", {"brightness": brightness})
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.12):
                await entry.runtime_data.client._confirm_light_settings((kitchen.entity_id,), {"brightness": 255})
    assert kitchen.calls == [] and bedroom.calls == []


async def test_followup_ids_are_per_robot_and_clear_on_disconnect_agent_change(hass, next_backend, monkeypatch):
    await release_devices(hass)
    agent = RecordingAgent()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await next_entry(hass, next_backend, monkeypatch)
    await options(hass, entry, conversation_agent=agent.entity_id)
    await voice(next_backend, "ask Home Assistant to say fixture greeting")

    async def followup_offered():
        captured = await snapshot(next_backend)
        return any(
            frame["type"] == "preferences" and any(item["available"] for item in frame.get("follow_up", []))
            for frame in captured["client_frames"]
        )

    await wait_until(followup_offered)
    await voice(next_backend, "and in the bedroom")
    assert len(agent.calls) == 2
    assert agent.calls[0].conversation_id is None
    assert agent.calls[1].conversation_id
    await voice(next_backend, "ask Home Assistant to say fixture greeting", robot=1)
    assert agent.calls[-1].conversation_id is None
    current = entry.runtime_data.client
    await control(next_backend, "/test/disconnect")
    await wait_for(lambda: current.state != "connected")
    await wait_for(lambda: current.state == "connected", seconds=8)
    await voice(next_backend, "ask Home Assistant to say fixture greeting")
    assert agent.calls[-1].conversation_id is None
    previous = await options(hass, entry, conversation_agent=conversation.HOME_ASSISTANT_AGENT)
    assert previous.stopping and previous.commands == {}
    await options(hass, entry, conversation_agent=agent.entity_id)
    await voice(next_backend, "ask Home Assistant to say fixture greeting")
    assert agent.calls[-1].conversation_id is None


async def test_real_followup_expires_and_exposure_is_rechecked(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await voice(next_backend, "turn on kitchen light")
    assert len(kitchen.calls) == 1 and bedroom.calls == []
    exposed_entities.async_expose_entity(hass, "conversation", kitchen.entity_id, False)
    await voice(next_backend, "turn it off")
    assert len(kitchen.calls) == 1
    exposed_entities.async_expose_entity(hass, "conversation", kitchen.entity_id, True)
    await voice(next_backend, "turn off kitchen light")
    assert len(kitchen.calls) == 2
    await asyncio.sleep(30.2)
    assert entry.runtime_data.client.contexts == {}
    await voice(next_backend, "turn it on")
    assert len(kitchen.calls) == 2 and bedroom.calls == []
    kitchen._attr_is_on = True
    kitchen._attr_brightness = 200
    kitchen.async_write_ha_state()
    commands_before = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    await voice(next_backend, "make it dimmer")
    commands_after = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    assert commands_after == commands_before and kitchen.calls[-1][0] == "off"
    assert hass.states.get(kitchen.entity_id).attributes["brightness"] == 200


async def test_relative_brightness_uses_each_cached_target_and_clamps(hass, next_backend, monkeypatch):
    kitchen, reading, bedroom = await relative_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    await voice(next_backend, "are the kitchen lights on")
    assert set(entry.runtime_data.client.contexts[robot_context(entry).robot_id].targets) == {
        kitchen.entity_id,
        reading.entity_id,
    }
    await voice(next_backend, "make them dimmer")
    assert kitchen.calls == [("on", {"brightness": 174})] and reading.calls == [("on", {"brightness": 54})]
    await voice(next_backend, "make them brighter")
    assert kitchen.calls[-1] == ("on", {"brightness": 200}) and reading.calls[-1] == ("on", {"brightness": 80})
    kitchen._attr_brightness = 20
    reading._attr_brightness = 250
    kitchen.async_write_ha_state()
    reading.async_write_ha_state()
    await voice(next_backend, "make those dimmer")
    assert hass.states.get(kitchen.entity_id).state == "off" and kitchen.calls[-1][0] == "off"
    assert reading.calls[-1] == ("on", {"brightness": 224})
    kitchen._attr_is_on = True
    kitchen._attr_brightness = 230
    reading._attr_brightness = 250
    kitchen.async_write_ha_state()
    reading.async_write_ha_state()
    await voice(next_backend, "make them brighter")
    assert kitchen.calls[-1] == reading.calls[-1] == ("on", {"brightness": 255})
    assert len(kitchen.calls) == len(reading.calls) == 4 and bedroom.calls == []


@pytest.mark.parametrize("invalid", ["unsupported", "off"])
async def test_relative_brightness_preflights_entire_group_before_services(hass, next_backend, monkeypatch, invalid):
    kitchen, reading, bedroom = await relative_devices(hass, invalid=invalid)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    await voice(next_backend, "are the kitchen lights on")
    targets = entry.runtime_data.client.contexts[robot_context(entry).robot_id].targets
    assert targets.index(kitchen.entity_id) < targets.index(reading.entity_id)
    await voice(next_backend, "make them dimmer")
    results = [
        frame["result"] for frame in (await snapshot(next_backend))["client_frames"] if frame["type"] == "result"
    ]
    assert results[-1]["outcome"] == "error"
    assert results[-1]["code"] == ("unsupported_feature" if invalid == "unsupported" else "brightness_unavailable")
    assert kitchen.calls == reading.calls == bedroom.calls == []
    assert not entry.runtime_data.client.contexts


async def test_relative_brightness_rejects_malformed_and_unavailable_group_values(hass, next_backend, monkeypatch):
    kitchen, reading, bedroom = await relative_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    cases = [
        ("missing", None, True, True, "brightness_unavailable"),
        ("bool", True, True, True, "brightness_unavailable"),
        ("nan", float("nan"), True, True, "brightness_unavailable"),
        ("infinity", float("inf"), True, True, "brightness_unavailable"),
        ("negative", -1, True, True, "brightness_unavailable"),
        ("above_maximum", 256, True, True, "brightness_unavailable"),
        ("unavailable", 80, False, True, "target_unavailable"),
        ("unknown", 80, True, None, "target_unavailable"),
    ]
    for label, brightness, available, is_on, expected_code in cases:
        reading._attr_available = True
        reading._attr_is_on = True
        reading._attr_brightness = 80
        reading.async_write_ha_state()
        await voice(next_backend, "are the kitchen lights on")
        assert set(entry.runtime_data.client.contexts[robot_context(entry).robot_id].targets) == {
            kitchen.entity_id,
            reading.entity_id,
        }
        # Publish the provider's malformed value through an actual LightEntity
        # after the read-only query has established the two-target context.
        reading._attr_available = available
        reading._attr_is_on = is_on
        reading._attr_brightness = brightness
        reading.async_write_ha_state()
        if label in ("unavailable", "unknown"):
            assert hass.states.get(reading.entity_id).state == label
        await voice(next_backend, "make them dimmer")
        results = [
            frame["result"] for frame in (await snapshot(next_backend))["client_frames"] if frame["type"] == "result"
        ]
        assert results[-1]["outcome"] == "error" and results[-1]["code"] == expected_code, label
        assert kitchen.calls == reading.calls == bedroom.calls == [], label
        assert not entry.runtime_data.client.contexts, label


async def test_relative_brightness_missing_other_robot_and_active_skill_context_never_dispatch(
    hass, next_backend, monkeypatch
):
    kitchen, reading, bedroom = await relative_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    await voice(next_backend, "make it dimmer")
    assert not any(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    await voice(next_backend, "are the kitchen lights on")
    commands_before = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    await voice(next_backend, "make them dimmer", robot=1)
    commands_after = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    assert commands_after == commands_before
    await voice(next_backend, "are the kitchen lights on")
    commands_before = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    assert entry.runtime_data.client.contexts
    await voice(next_backend, "make them dimmer", skill="invented-active-skill", hotphrase=False)
    commands_after = sum(frame["type"] == "command" for frame in (await snapshot(next_backend))["server_frames"])
    assert commands_after == commands_before and kitchen.calls == reading.calls == bedroom.calls == []


async def test_relative_brightness_lost_confirmation_is_uncertain_and_never_retried(hass, next_backend, monkeypatch):
    await release_devices(hass)
    light = SyntheticLight("Kitchen Unconfirmed Light", "invented-relative-unconfirmed")
    light._attr_is_on = True
    light._attr_brightness = 200
    await hass.data["light"].async_add_entities([light])
    exposed_entities.async_expose_entity(hass, "conversation", light.entity_id, True)

    async def unconfirmed(**kwargs):
        light.calls.append(("on", kwargs))

    monkeypatch.setattr(light, "async_turn_on", unconfirmed)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    await voice(next_backend, "is the kitchen unconfirmed light on")
    started = time.monotonic()
    await voice(next_backend, "make it dimmer")
    elapsed = time.monotonic() - started
    results = [
        frame["result"] for frame in (await snapshot(next_backend))["client_frames"] if frame["type"] == "result"
    ]
    assert 7 <= elapsed <= 10 and results[-1]["outcome"] == "uncertain"
    assert results[-1]["code"] == "confirmation_lost"
    await asyncio.sleep(0.2)
    assert light.calls == [("on", {"brightness": 174})]
    assert hass.states.get(light.entity_id).attributes["brightness"] == 200
    assert not entry.runtime_data.client.contexts


@pytest.mark.parametrize("change", ["exposure", "off", "brightness"])
async def test_relative_brightness_pending_target_change_after_first_dispatch_stops_group(
    hass, next_backend, monkeypatch, change
):
    kitchen, reading, bedroom = await relative_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    assign_room(hass, entry, "Kitchen")
    await voice(next_backend, "are the kitchen lights on")
    targets = entry.runtime_data.client.contexts[robot_context(entry).robot_id].targets
    entities = {light.entity_id: light for light in (kitchen, reading)}
    first, second = (entities[entity_id] for entity_id in targets)
    original = first.async_turn_on

    async def change_second(**kwargs):
        await original(**kwargs)
        if change == "exposure":
            exposed_entities.async_expose_entity(hass, "conversation", second.entity_id, False)
        elif change == "off":
            second._attr_is_on = False
            second.async_write_ha_state()
        else:
            second._attr_brightness += 1
            second.async_write_ha_state()

    monkeypatch.setattr(first, "async_turn_on", change_second)
    await voice(next_backend, "make them dimmer")
    results = [
        frame["result"] for frame in (await snapshot(next_backend))["client_frames"] if frame["type"] == "result"
    ]
    assert results[-1]["outcome"] == "partial" and results[-1]["code"] == "confirmation_lost"
    assert len(first.calls) == 1 and second.calls == bedroom.calls == []
    assert not entry.runtime_data.client.contexts


async def test_failed_new_target_clears_previous_followup(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await voice(next_backend, "turn on kitchen light")
    assert entry.runtime_data.client.contexts and len(kitchen.calls) == 1
    await voice(next_backend, "turn on imaginary light")
    assert entry.runtime_data.client.contexts == {}
    await voice(next_backend, "turn it off")
    assert len(kitchen.calls) == 1 and bedroom.calls == []


async def test_oversized_resolved_target_set_withholds_followup(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    lights = [SyntheticLight(f"Kitchen Lamp {index:02}", f"invented-overflow-light-{index}") for index in range(33)]
    await hass.data["light"].async_add_entities(lights)
    area = ar.async_get(hass).async_get_area_by_name("Kitchen")
    for light in lights:
        er.async_get(hass).async_update_entity(light.entity_id, area_id=area.id)
        exposed_entities.async_expose_entity(hass, "conversation", light.entity_id, True)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await voice(next_backend, "turn on kitchen lights")
    assert len(kitchen.calls) == 1 and all(len(light.calls) == 1 for light in lights) and bedroom.calls == []
    assert entry.runtime_data.client.contexts == {}
    await voice(next_backend, "turn them off")
    assert all(len(light.calls) == 1 for light in [kitchen, *lights])


async def test_real_hs_light_cached_color_and_brightness_confirmation(hass, next_backend, monkeypatch):
    await release_devices(hass)
    light = SyntheticHSLight("Normalized Color Light", "invented-normalized-color")
    await hass.data["light"].async_add_entities([light])
    exposed_entities.async_expose_entity(hass, "conversation", light.entity_id, True)
    await next_entry(hass, next_backend, monkeypatch)
    assert "off" in speech(await voice(next_backend, "is normalized color light on")).lower()
    assert light.calls == []
    assert "couldn't" not in speech(await voice(next_backend, "set it to green"))
    assert tuple(hass.states.get(light.entity_id).attributes["hs_color"]) == (120, 100)
    assert tuple(hass.states.get(light.entity_id).attributes["rgb_color"]) == (0, 255, 0)
    assert "couldn't" not in speech(await voice(next_backend, "set it brightness to 25 percent"))
    assert hass.states.get(light.entity_id).attributes["brightness"] == 64
    assert len(light.calls) == 2


async def test_reverse_announcement_permission_current_volume_and_honest_confirmation(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True, stranger=True)
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.client.async_announce(robot_context(entry).robot_id, "Invented announcement.")
    assert (await snapshot(next_backend))["dispatches"] == []
    await owner_permission(next_backend, entry, True)
    await options(hass, entry, conversation_agent=conversation.HOME_ASSISTANT_AGENT)
    start = time.perf_counter()
    await entry.runtime_data.client.async_announce(
        robot_context(entry).robot_id, "Invented announcement with <angle> & text."
    )
    elapsed = (time.perf_counter() - start) * 1000
    print(json.dumps({"synthetic_confirmed_announcement_ms": round(elapsed, 1), "ha_version": HA_VERSION}))
    captured = await snapshot(next_backend)
    assert len(captured["dispatches"]) == 1
    assert "volume" not in captured["dispatches"][0]
    assert set(captured["dispatches"][0]["native_keys"]) == {
        "v",
        "type",
        "request_id",
        "text",
        "deadline_ms",
    }
    assert all("volume" not in frame for frame in captured["client_frames"] if frame["type"] == "robot_action")
    await control(
        next_backend, "/test/control", {"robot": next_backend[0]["robots"][0]["friendly_id"], "mode": "unconfirmed"}
    )
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.client.async_announce(
            robot_context(entry).robot_id, "Unconfirmed invented announcement."
        )
    assert len((await snapshot(next_backend))["dispatches"]) == 2


async def test_legacy_volume_requests_are_rejected_without_admission(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    client = entry.runtime_data.client
    idle = await telemetry(next_backend)
    authorization_requests = (await snapshot(next_backend))["authorization_requests"]
    for volume in [0, None, 0.5, "high"]:
        request_id = str(uuid4())
        await client._send(
            {
                "type": "robot_action",
                "action": "announce",
                "request_id": request_id,
                "robot_id": robot_context(entry).robot_id,
                "text": "Invented unsupported legacy volume.",
                "volume": volume,
                "deadline_ms": int(time.time() * 1000) + 30000,
            }
        )
        result = None

        async def rejected():
            nonlocal result
            result = next(
                (
                    frame["result"]
                    for frame in (await snapshot(next_backend))["server_frames"]
                    if frame["type"] == "action_result" and frame["request_id"] == request_id
                ),
                None,
            )
            return result is not None

        await wait_until(rejected)
        assert result["outcome"] == "error" and result["code"] == "unsupported_volume"
        assert client.state == "connected" and (await snapshot(next_backend))["dispatches"] == []
        assert (await snapshot(next_backend))["authorization_requests"] == authorization_requests
        state = await telemetry(next_backend)
        assert state["active"] == 0 and state["lastActivityAt"] == idle["lastActivityAt"]
        ledger = Path(next_backend[0]["runtime_dir"]).parent.joinpath("account.json").read_text()
        assert request_id not in ledger
    await client.async_announce(robot_context(entry).robot_id, "Invented current-volume announcement.")
    captured = await snapshot(next_backend)
    assert len(captured["dispatches"]) == 1 and "volume" not in captured["dispatches"][0]["native_keys"]
    request_id = captured["dispatches"][0]["request_id"]
    replies_before = sum(
        frame["type"] == "action_result" and frame["request_id"] == request_id for frame in captured["server_frames"]
    )
    await client._send(
        {
            "type": "robot_action",
            "action": "announce",
            "request_id": request_id,
            "robot_id": robot_context(entry).robot_id,
            "text": "Invented current-volume announcement.",
            "volume": 0,
            "deadline_ms": int(time.time() * 1000) + 30000,
        }
    )

    async def cached_id_rejected():
        replies = [
            frame["result"]
            for frame in (await snapshot(next_backend))["server_frames"]
            if frame["type"] == "action_result" and frame["request_id"] == request_id
        ]
        return len(replies) > replies_before and replies[-1].get("code") == "unsupported_volume"

    await wait_until(cached_id_rejected)
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    assert (await snapshot(next_backend))["authorization_requests"] == authorization_requests + 1


async def test_legacy_volume_option_is_ignored_and_removed_on_save(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    previous = entry.runtime_data.client
    hass.config_entries.async_update_entry(entry, options={**entry.options, "announcement_volume": 35})
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.state == "connected")
    await wait_for(lambda: len(entry.runtime_data.client.robots) == 2)
    await entry.runtime_data.client.async_announce(robot_context(entry).robot_id, "Invented legacy-options fixture.")
    captured = await snapshot(next_backend)
    assert len(captured["dispatches"]) == 1
    assert "volume" not in captured["dispatches"][0]["native_keys"]
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert all(marker.schema != "announcement_volume" for marker in flow["data_schema"].schema)
    hass.config_entries.options.async_abort(flow["flow_id"])
    await options(hass, entry, conversation_agent=conversation.HOME_ASSISTANT_AGENT)
    assert "announcement_volume" not in entry.options


async def test_private_bridge_volume_requests_never_authorize_or_reserve(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    data, session = next_backend
    idle = await telemetry(next_backend)
    authorization_requests = (await snapshot(next_backend))["authorization_requests"]
    for volume in [0, None, 0.5]:
        request_id = str(uuid4())
        async with session.post(
            data["gateway_url"] + "/internal/home-assistant/robot-action/announce",
            headers={"x-phoenix-internal-token": "synthetic-next-peer"},
            json={
                "identity": data["robots"][0]["identity"],
                "requestId": request_id,
                "authorizationId": f"{entry.data['installation_id']}:{request_id}",
                "text": "Invented unsupported private volume.",
                "volume": volume,
                "deadline": int(time.time() * 1000) + 30000,
            },
        ) as response:
            assert response.status == 200
            result = await response.json()
        assert result == {"outcome": "error", "code": "unsupported_volume"}
        assert (await snapshot(next_backend))["dispatches"] == []
        assert (await snapshot(next_backend))["authorization_requests"] == authorization_requests
        state = await telemetry(next_backend)
        assert state["active"] == 0 and state["lastActivityAt"] == idle["lastActivityAt"]
        reservations = Path(data["runtime_dir"]).joinpath("robot-actions", "outstanding.json").read_text()
        assert request_id not in reservations


async def test_native_notify_entity_and_quiet_hours_never_dispatch(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    notify_entities = [state.entity_id for state in hass.states.async_all("notify")]
    assert len(notify_entities) == 2
    await hass.services.async_call(
        "notify", "send_message", {"entity_id": notify_entities[0], "message": "Invented native notify."}, blocking=True
    )
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    now = dt_util.now()
    await options(
        hass,
        entry,
        conversation_agent=conversation.HOME_ASSISTANT_AGENT,
        quiet_hours_enabled=True,
        quiet_hours_start=(now - timedelta(hours=1)).strftime("%H:%M:%S"),
        quiet_hours_end=(now + timedelta(hours=1)).strftime("%H:%M:%S"),
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "notify", "send_message", {"entity_id": notify_entities[0], "message": "Must stay quiet."}, blocking=True
        )
    assert len((await snapshot(next_backend))["dispatches"]) == 1


async def test_announcement_status_remains_visible_while_notify_is_unavailable(hass, next_backend, monkeypatch):
    assert await async_setup_component(hass, "conversation", {})
    entry = await next_entry(hass, next_backend, monkeypatch)
    robot_id = robot_context(entry).robot_id
    fixture_robot = next_backend[0]["robots"][0]["friendly_id"]
    await control(
        next_backend,
        "/test/roster-status",
        {"robot": fixture_robot, "online": True, "busy": False, "announcements_supported": False},
    )
    await owner_permission(next_backend, entry, True)
    await announcement_status(hass, entry, robot_id, "firmware_required", notify_available=False)
    with pytest.raises(ServiceValidationError) as unsupported:
        await entry.runtime_data.client.async_announce(robot_id, "Blocked invented receiver.")
    assert unsupported.value.translation_key == "unsupported_robot_announcements"
    await owner_permission(next_backend, entry, False)
    await announcement_status(hass, entry, robot_id, "permission_required", notify_available=False)
    with pytest.raises(ServiceValidationError) as disallowed:
        await entry.runtime_data.client.async_announce(robot_id, "Blocked invented permission.")
    assert disallowed.value.translation_key == "permission_denied"
    await owner_permission(next_backend, entry, True)
    await announcement_status(hass, entry, robot_id, "firmware_required", notify_available=False)
    await control(
        next_backend,
        "/test/roster-status",
        {"robot": fixture_robot, "online": True, "busy": True, "announcements_supported": True},
    )
    await announcement_status(hass, entry, robot_id, "busy", notify_available=True)
    with pytest.raises(ServiceValidationError) as busy:
        await entry.runtime_data.client.async_announce(robot_id, "Blocked invented busy receiver.")
    assert busy.value.translation_key == "robot_busy"
    await control(
        next_backend,
        "/test/roster-status",
        {"robot": fixture_robot, "online": False, "busy": False, "announcements_supported": True},
    )
    await announcement_status(hass, entry, robot_id, "offline", notify_available=False)
    await control(next_backend, "/test/disconnect")
    await announcement_status(hass, entry, robot_id, "disconnected", notify_available=False)
    await announcement_status(hass, entry, robot_id, "offline", notify_available=False, seconds=10)
    captured = await snapshot(next_backend)
    assert captured["dispatches"] == [] and captured["authorization_requests"] == 0
    assert not any(frame["type"] == "robot_action" for frame in captured["client_frames"])
    assert not entry.runtime_data.client.pending_actions
    timers = await announcement_timers(hass, entry)
    assert (await hass.config_entries.async_remove(entry.entry_id))["require_restart"] is False
    assert_announcement_timers_removed(hass, timers)


async def test_announcement_status_local_quiet_tick_and_interval_cleanup(hass, next_backend, monkeypatch):
    assert await async_setup_component(hass, "conversation", {})
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    await control(next_backend, "/test/broker-timer", {"pause": True})
    try:
        now = dt_util.now()
        quiet_end = now + timedelta(seconds=5)
        await options(
            hass,
            entry,
            conversation_agent=conversation.HOME_ASSISTANT_AGENT,
            quiet_hours_enabled=True,
            quiet_hours_start=(now - timedelta(minutes=1)).strftime("%H:%M:%S"),
            quiet_hours_end=quiet_end.strftime("%H:%M:%S"),
        )
        robot_ids = list(entry.runtime_data.client.robots)
        initial = [
            await announcement_status(hass, entry, robot_id, "quiet_hours", notify_available=True)
            for robot_id in robot_ids
        ]
        old_timers = await announcement_timers(hass, entry)
        before = await snapshot(next_backend)
        assert before["broker_timer_paused"]
        started = time.perf_counter()
        while dt_util.now() <= quiet_end + timedelta(milliseconds=100):
            await asyncio.sleep(0.05)
        assert not entry.runtime_data.client._quiet_hours()
        assert all(hass.states.get(state.entity_id).last_updated == state.last_updated for state in initial)
        for robot_id in robot_ids:
            await announcement_status(hass, entry, robot_id, "ready", notify_available=True, seconds=40)
        after = await snapshot(next_backend)
        assert after["broker_timer_paused"]
        assert after["status_reads"] == before["status_reads"]
        assert after["controls"] == before["controls"]
        assert [frame for frame in after["server_frames"] if frame["type"] == "roster"] == [
            frame for frame in before["server_frames"] if frame["type"] == "roster"
        ]
        assert after["dispatches"] == [] and after["authorization_requests"] == 0
        assert not any(frame["type"] == "robot_action" for frame in after["client_frames"])
        print(
            json.dumps(
                {
                    "case": "real_local_status_quiet_boundary",
                    "ha_version": HA_VERSION,
                    "observed_seconds": round(time.perf_counter() - started, 3),
                    "local_interval_seconds": 30,
                    "broker_status_reads_before_and_after": before["status_reads"],
                    "unchanged_roster_frames": True,
                    "outgoing_actions": 0,
                }
            )
        )
    finally:
        await control(next_backend, "/test/broker-timer", {"pause": False})
    await options(hass, entry, conversation_agent=conversation.HOME_ASSISTANT_AGENT, quiet_hours_enabled=False)
    assert_announcement_timers_removed(hass, old_timers)
    replacement = await announcement_timers(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert_announcement_timers_removed(hass, replacement)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: len(entry.runtime_data.client.robots) == 2)
    replacement = await announcement_timers(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert_announcement_timers_removed(hass, replacement)


@pytest.mark.parametrize("lost_state", ["unavailable", "missing", "unknown", "later_missing"])
async def test_native_onoff_lost_state_is_uncertain_and_never_retried(hass, next_backend, monkeypatch, lost_state):
    """A real exposed light loses its state after builtin service dispatch."""
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "light", {})

    class LostConfirmationLight(SyntheticLight):
        async def async_turn_on(self, **kwargs):
            if lost_state == "later_missing":
                self.calls.append(("on", kwargs))
                self.async_on_remove(
                    self.hass.loop.call_later(0.1, self.hass.states.async_remove, self.entity_id).cancel
                )
                return  # Service acknowledged, state still off while confirmation starts.
            await super().async_turn_on(**kwargs)
            if lost_state == "missing":
                self.hass.states.async_remove(self.entity_id)
            else:
                if lost_state == "unavailable":
                    self._attr_available = False
                else:
                    self._attr_is_on = None
                self.async_write_ha_state()

    light = LostConfirmationLight("Confirmation Light", "invented-post-dispatch-confirmation")
    await hass.data["light"].async_add_entities([light])
    exposed_entities.async_expose_entity(hass, "conversation", light.entity_id, True)
    original_converse = conversation.async_converse
    answers = []

    async def observe_converse(*args, **kwargs):
        answer = await original_converse(*args, **kwargs)
        answers.append(answer)
        return answer

    monkeypatch.setattr(conversation, "async_converse", observe_converse)
    entry = await next_entry(hass, next_backend, monkeypatch)
    request_id = str(uuid4())
    deadline_seconds = 7.5  # Use the normal voice budget, including cold builtin preparation.
    started = time.perf_counter()
    await control(
        next_backend,
        "/test/inject-command",
        {
            "request_id": request_id,
            "robot_id": robot_context(entry).robot_id,
            "text": "turn on Confirmation Light",
            "language": "en",
            "deadline_ms": int(time.time() * 1000 + deadline_seconds * 1000),
            "route": {"kind": "command"},
        },
    )

    async def completed():
        return any(
            frame["type"] == "result" and frame["request_id"] == request_id
            for frame in (await snapshot(next_backend))["client_frames"]
        )

    await wait_until(completed, seconds=deadline_seconds + 1)
    elapsed = time.perf_counter() - started
    captured = await snapshot(next_backend)
    result = next(
        frame["result"]
        for frame in captured["client_frames"]
        if frame["type"] == "result" and frame["request_id"] == request_id
    )
    state = hass.states.get(light.entity_id)
    resolved = (
        [target.id for target in answers[0].response.success_results if target.type == "entity"] if answers else []
    )
    print(
        json.dumps(
            {
                "case": "native_onoff_post_dispatch_state_loss",
                "ha_version": HA_VERSION,
                "lost_state": lost_state,
                "actual_builtin_success_ids": resolved,
                "service_calls": len(light.calls),
                "completed_builtin_answers": len(answers),
                "elapsed_seconds": round(elapsed, 3),
                "original_deadline_budget_seconds": deadline_seconds,
                "actual_wire_result": result,
            }
        )
    )
    assert len(light.calls) == len(answers) == 1, result
    assert answers[0].response.intent.intent_type == "HassTurnOn"
    assert resolved == [light.entity_id]
    assert (state is None) if lost_state.endswith("missing") else state.state == lost_state
    assert result["outcome"] == "uncertain"
    assert elapsed <= deadline_seconds + 1
    if lost_state == "later_missing":
        assert result["code"] == "confirmation_lost"
        assert elapsed >= deadline_seconds - 0.25
    assert not entry.runtime_data.client.contexts
    await asyncio.sleep(0.1)
    assert len(light.calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reverse_durable_dedup_expired_wrong_binding_and_busy(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    client = entry.runtime_data.client
    robot_id = robot_context(entry).robot_id
    request_id = str(uuid4())
    frame = {
        "type": "robot_action",
        "action": "announce",
        "request_id": request_id,
        "robot_id": robot_id,
        "text": "Invented one-time announcement.",
        "deadline_ms": int(time.time() * 1000) + 30000,
    }
    await client._send(frame)

    async def result_received(request):
        return any(
            item["type"] == "action_result" and item["request_id"] == request
            for item in (await snapshot(next_backend))["server_frames"]
        )

    await wait_until(lambda: result_received(request_id))
    await client._send({**frame, "text": "A changed duplicate must not speak."})
    await asyncio.sleep(0.1)
    observed = await snapshot(next_backend)
    assert len(observed["dispatches"]) == 1
    assert observed["dispatches"][0]["persisted_before_dispatch"]
    store = json.loads(Path(next_backend[0]["runtime_dir"]).parent.joinpath("account.json").read_text())
    assert request_id in json.dumps(store), "The broker must persist deduplication before dispatch"
    # A separate real TLS broker is reconstructed from the persisted snapshot.
    # Its recovered installation cannot execute the same admitted request again.
    recovered = await control(next_backend, "/test/recovery")
    async with next_backend[1].ws_connect(
        recovered["url"].replace("https://", "wss://") + "/api/home-assistant/connect",
        headers={"Authorization": f"Bearer {entry.data['credential']}"},
    ) as socket:
        welcome = await socket.receive_json()
        await socket.send_json(
            {
                "v": 1,
                "type": "ready",
                "session_id": welcome["session_id"],
                "agent": "home_assistant",
                "ha_version": HA_VERSION,
                "capabilities": ["robot_roster", "robot_action"],
            }
        )
        await socket.send_json({"v": 1, "session_id": welcome["session_id"], **frame})
        async with asyncio.timeout(5):
            while True:
                reply = await socket.receive_json()
                if reply["type"] == "action_result":
                    assert reply["request_id"] == request_id and reply["result"]["outcome"] == "success"
                    break
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    for payload in [
        {**frame, "request_id": str(uuid4()), "deadline_ms": 1},
        {**frame, "request_id": str(uuid4()), "robot_id": str(uuid4())},
    ]:
        await client._send(payload)
        await wait_until(lambda payload=payload: result_received(payload["request_id"]))
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    await control(next_backend, "/test/control", {"robot": next_backend[0]["robots"][0]["friendly_id"], "mode": "hold"})
    announcement = asyncio.create_task(client.async_announce(robot_id, "Held invented announcement."))

    async def held():
        return bool((await snapshot(next_backend))["held"])

    await wait_until(held)
    with pytest.raises(ServiceValidationError):
        await client.async_announce(robot_id, "Must reject while busy.")
    captured = await snapshot(next_backend)
    await control(next_backend, "/test/release", {"request_id": captured["held"][0]})
    await announcement
    assert len((await snapshot(next_backend))["dispatches"]) == 2


async def test_ownership_transfer_revokes_reverse_permission_and_credential(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    client = entry.runtime_data.client
    await client.async_announce(robot_context(entry).robot_id, "Invented pre-transfer confirmation.")
    await control(next_backend, "/test/transfer", {"robot": next_backend[0]["robots"][0]["friendly_id"]})
    await wait_for(lambda: client.state == "authentication_required")
    with pytest.raises(ServiceValidationError):
        await client.async_announce(robot_context(entry).robot_id, "Must never dispatch after transfer.")
    data, session = next_backend
    async with session.delete(
        data["url"] + "/api/home-assistant/installation",
        headers={"Authorization": f"Bearer {entry.data['credential']}"},
    ) as response:
        assert response.status == 401
    assert len((await snapshot(next_backend))["dispatches"]) == 1


@pytest.mark.parametrize("change", ["disable_permission", "ownership_transfer"])
async def test_live_permission_is_rechecked_after_gateway_verification(hass, next_backend, monkeypatch, change):
    """A real held HTTP verification cannot outlive the owner's authorization."""
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    await control(next_backend, "/test/verify-hold", {"enabled": True})
    announcement = asyncio.create_task(
        entry.runtime_data.client.async_announce(robot_context(entry).robot_id, "Must not outlive owner authorization.")
    )

    async def verification_is_held():
        return (await snapshot(next_backend))["verify_waiting"] > 0

    await wait_until(verification_is_held)
    assert (await telemetry(next_backend))["active"] == 0
    if change == "disable_permission":
        await owner_permission(next_backend, entry, False)
    else:
        await control(next_backend, "/test/transfer", {"robot": next_backend[0]["robots"][0]["friendly_id"]})
    await control(next_backend, "/test/verify-hold", {"enabled": False})
    with pytest.raises(HomeAssistantError) as refused:
        await announcement
    if change == "disable_permission":
        assert isinstance(refused.value, ServiceValidationError)

    async def verification_finished():
        return (await snapshot(next_backend))["verify_waiting"] == 0

    await wait_until(verification_finished)
    await asyncio.sleep(0.1)
    assert (await snapshot(next_backend))["dispatches"] == []
    assert (await telemetry(next_backend))["active"] == 0


async def test_reverse_disconnect_revocation_and_unload_never_replay(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    await control(next_backend, "/test/control", {"robot": next_backend[0]["robots"][0]["friendly_id"], "mode": "hold"})
    client = entry.runtime_data.client
    announcement = asyncio.create_task(
        client.async_announce(robot_context(entry).robot_id, "Do not replay after reconnect.")
    )

    async def held():
        return bool((await snapshot(next_backend))["held"])

    await wait_until(held)
    await control(next_backend, "/test/disconnect")
    with pytest.raises(HomeAssistantError):
        await announcement
    await wait_for(lambda: client.state == "connected", seconds=8)
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    await control(next_backend, "/test/revoke")
    await wait_for(lambda: client.state == "authentication_required")
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client.stopping and client.commands == {} and client.session.closed


async def test_voice_deadline_has_no_retry_and_diagnostics_have_no_private_context(hass, next_backend, monkeypatch):
    kitchen, bedroom, _ = await release_devices(hass)
    agent = RecordingAgent()
    agent.hold = asyncio.Event()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await next_entry(hass, next_backend, monkeypatch)
    await options(hass, entry, conversation_agent=agent.entity_id)
    frames = await voice(next_backend, "ask Home Assistant to wait for fixture completion")
    assert "couldn't confirm" in speech(frames)
    assert len(agent.calls) == 1 and kitchen.calls == [] and bedroom.calls == []
    captured = await snapshot(next_backend)
    commands = [frame for frame in captured["server_frames"] if frame["type"] == "command"]
    assert len(commands) == 1
    # The HA deadline uses the broker's server clock, and stays at 7.5 seconds.
    assert commands[0]["deadline_ms"] - int(time.time() * 1000) <= 50
    report = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(report)
    for private_value in [
        entry.data["credential"],
        robot_context(entry).robot_id,
        "fixture completion",
        "Kitchen",
        "scene.dinner",
    ]:
        assert private_value not in serialized
    await asyncio.sleep(0.1)
    assert len(agent.calls) == 1


async def test_forward_transactions_count_activity_and_idle_connector_does_not(hass, next_backend, monkeypatch):
    await release_devices(hass)
    agent = RecordingAgent()
    agent.hold = asyncio.Event()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await next_entry(hass, next_backend, monkeypatch)
    await options(hass, entry, conversation_agent=agent.entity_id)
    data, session = next_backend

    async def activity():
        async with session.get(data["url"] + "/test/telemetry") as response:
            assert response.status == 200
            return await response.json()

    idle = await activity()
    assert idle["active"] == 0
    pending_turn = asyncio.create_task(voice(next_backend, "ask Home Assistant to say fixture activity"))
    await asyncio.wait_for(agent.started.wait(), 3)
    assert (await activity())["active"] >= 1
    agent.hold.set()
    await pending_turn
    await wait_until(lambda: activity_is_idle(activity))
    completed = await activity()
    assert completed["lastActivityAt"] >= idle["lastActivityAt"]
    await asyncio.sleep(1.1)
    heartbeats = await activity()
    assert heartbeats["active"] == 0 and heartbeats["lastActivityAt"] == completed["lastActivityAt"]


async def activity_is_idle(activity):
    return (await activity())["active"] == 0


async def telemetry(backend):
    data, session = backend
    async with session.get(data["url"] + "/test/telemetry") as response:
        assert response.status == 200, await response.text()
        return await response.json()


async def test_real_reverse_bridge_counts_activity_and_respects_deployment_drain(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    await control(next_backend, "/test/control", {"robot": next_backend[0]["robots"][0]["friendly_id"], "mode": "hold"})
    client = entry.runtime_data.client
    announcement = asyncio.create_task(client.async_announce(robot_context(entry).robot_id, "Held deployment fixture."))

    async def held():
        return bool((await snapshot(next_backend))["held"])

    await wait_until(held)
    assert (await telemetry(next_backend))["active"] >= 1
    await control(next_backend, "/test/drain", {"enabled": True})
    with pytest.raises(ServiceValidationError):
        await client.async_announce(robot_context(entry, 1).robot_id, "Must reject during deployment drain.")
    with pytest.raises(aiohttp.WSServerHandshakeError) as rejected:
        await voice(next_backend, "turn on kitchen light", robot=1)
    assert rejected.value.status == 503
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    await control(next_backend, "/test/drain", {"enabled": False})
    request = (await snapshot(next_backend))["held"][0]
    await control(next_backend, "/test/release", {"request_id": request})
    await announcement

    async def idle():
        return (await telemetry(next_backend))["active"] == 0

    await wait_until(idle)
    completed = await telemetry(next_backend)
    await asyncio.sleep(1.1)
    observed = await telemetry(next_backend)
    assert observed["active"] == 0 and observed["lastActivityAt"] == completed["lastActivityAt"]


async def test_reverse_deadline_without_native_stop_keeps_voice_and_guard_busy(hass, next_backend, monkeypatch):
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    fixture_robot = next_backend[0]["robots"][0]["friendly_id"]
    await control(next_backend, "/test/control", {"robot": fixture_robot, "mode": "hold", "cancel_mode": "hold"})
    request_id = str(uuid4())
    frame = {
        "type": "robot_action",
        "action": "announce",
        "request_id": request_id,
        "robot_id": robot_context(entry).robot_id,
        "text": "Invented failed-stop announcement.",
        "deadline_ms": int(time.time() * 1000) + 750,
    }
    await entry.runtime_data.client._send(frame)

    async def uncertain():
        return any(
            item["type"] == "action_result"
            and item["request_id"] == request_id
            and item["result"]["outcome"] == "uncertain"
            for item in (await snapshot(next_backend))["server_frames"]
        )

    await wait_until(uncertain, seconds=4)
    assert (await telemetry(next_backend))["active"] >= 1
    with pytest.raises(aiohttp.WSServerHandshakeError) as rejected:
        await voice(next_backend, "turn on kitchen light")
    assert rejected.value.status == 503
    assert (await telemetry(next_backend))["active"] >= 1
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    # Only an actual new native idle report may release admission after expiry.
    await control(next_backend, "/test/control", {"robot": fixture_robot, "executing": False})

    async def idle():
        return (await telemetry(next_backend))["active"] == 0

    await wait_until(idle)
    await control(next_backend, "/test/release", {"request_id": request_id})
    await entry.runtime_data.client._send(frame)
    await asyncio.sleep(0.1)
    results = [
        item["result"]["outcome"]
        for item in (await snapshot(next_backend))["server_frames"]
        if item["type"] == "action_result" and item["request_id"] == request_id
    ]
    assert results and all(value == "uncertain" for value in results)
    assert len((await snapshot(next_backend))["dispatches"]) == 1


async def test_actual_full_quiet_minute_with_live_idle_sockets(hass, next_backend, monkeypatch):
    """Run the unchanged production guard over fresh Hub/OTA reports for 60s."""
    await release_devices(hass)
    entry = await next_entry(hass, next_backend, monkeypatch)
    await owner_permission(next_backend, entry, True)
    await control(next_backend, "/test/control", {"robot": next_backend[0]["robots"][0]["friendly_id"], "mode": "hold"})
    announcement = asyncio.create_task(
        entry.runtime_data.client.async_announce(robot_context(entry).robot_id, "Quiet-minute fixture completion.")
    )

    async def held():
        return bool((await snapshot(next_backend))["held"])

    await wait_until(held)
    started = await control(next_backend, "/test/guard")
    await asyncio.sleep(1.1)
    request = (await snapshot(next_backend))["held"][0]
    await control(next_backend, "/test/release", {"request_id": request})
    await announcement
    await voice(next_backend, "turn on kitchen light")

    async def idle():
        return (await telemetry(next_backend))["active"] == 0

    await wait_until(idle)
    last_activity = (await telemetry(next_backend))["lastActivityAt"]
    data, session = next_backend
    finished = {}

    async def guard_finished():
        nonlocal finished
        async with session.get(data["url"] + "/test/guard") as response:
            assert response.status == 200
            finished = await response.json()
        assert not finished.get("error"), finished
        return bool(finished.get("settled_at_ms"))

    await wait_until(guard_finished, seconds=70, interval=0.5)
    assert finished["claimed_at_ms"] - last_activity >= 60000
    assert finished["claimed_at_ms"] - started["started_at_ms"] >= 60000
    assert (await telemetry(next_backend))["active"] == 0
    print(
        json.dumps(
            {
                "observed_production_guard_quiet_ms": finished["claimed_at_ms"] - last_activity,
                "ha_version": HA_VERSION,
                "physical_robot": False,
                "service_restart": False,
            }
        )
    )


async def test_actual_gateway_process_replacement_recovers_native_execution_and_quiet_guard(
    hass, next_backend, monkeypatch
):
    await release_devices(hass)
    selected = RecordingAgent()
    await hass.data["conversation"].async_add_entities([selected])
    entry = await next_entry(hass, next_backend, monkeypatch)
    await options(hass, entry, conversation_agent=selected.entity_id)
    await owner_permission(next_backend, entry, True)
    started = await control(next_backend, "/test/restart-start")
    assert started["startup_activity_counts"] and all(count == 0 for count in started["startup_activity_counts"])
    data, session = next_backend
    restarted_backend = ({**data, "gateway_url": started["gateway_url"]}, session)
    robot = data["robots"][0]["friendly_id"]

    async def restart_state():
        async with session.get(data["url"] + "/test/restart-state") as response:
            assert response.status == 200, await response.text()
            return await response.json()

    idle = await restart_state()
    await control(next_backend, "/test/control", {"robot": robot, "busy": True})
    await asyncio.sleep(1.2)
    generic_busy = await restart_state()
    assert generic_busy["active"] == 0 and generic_busy["lastActivityAt"] == idle["lastActivityAt"]
    await control(next_backend, "/test/control", {"robot": robot, "busy": False, "mode": "hold", "cancel_mode": "hold"})
    announcement = asyncio.create_task(
        entry.runtime_data.client.async_announce(robot_context(entry).robot_id, "Invented retained native speech.")
    )

    async def dispatched():
        return len((await snapshot(next_backend))["dispatches"]) == 1

    await wait_until(dispatched)
    request_id = (await snapshot(next_backend))["dispatches"][0]["request_id"]
    active = await restart_state()
    assert active["active"] == 1
    reservation_dir = Path(started["runtime_dir"]) / "robot-actions"
    persisted = "\n".join(file.read_text() for file in reservation_dir.rglob("*.json"))
    assert request_id in persisted and "Invented retained native speech." not in persisted and '"text"' not in persisted
    replaced = await control(next_backend, "/test/restart", {"draining": True, "reconnect": False})
    assert replaced["pid"] != started["pid"] and replaced["gateway_url"] == started["gateway_url"]
    assert replaced["startup_activity_counts"] and all(count == 1 for count in replaced["startup_activity_counts"])
    with pytest.raises(HomeAssistantError):
        await announcement
    offline = await restart_state()
    assert offline["hub"]["instance"] != active["hub"]["instance"]
    assert offline["active"] == 1 and offline["hub"]["active"]["robot-action"] == 1
    assert offline["hub"]["drainId"] == "synthetic-recovered-drain"
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    with pytest.raises(aiohttp.WSServerHandshakeError) as draining_voice:
        await session.ws_connect(
            started["gateway_url"].replace("http://", "ws://") + "/v1/listen",
            headers={"Authorization": f"Bearer {data['robots'][0]['robot_token']}"},
        )
    assert draining_voice.value.status == 503
    await control(next_backend, "/test/restart-drain", {"enabled": False})
    with pytest.raises(aiohttp.WSServerHandshakeError) as offline_voice:
        await session.ws_connect(
            started["gateway_url"].replace("http://", "ws://") + "/v1/listen",
            headers={"Authorization": f"Bearer {data['robots'][0]['robot_token']}"},
        )
    assert offline_voice.value.status == 503
    await control(next_backend, "/test/restart-drain", {"enabled": True})
    offline = await restart_state()
    assert offline["active"] == 1
    await control(next_backend, "/test/restart-guard")
    offline_started = time.monotonic()
    while time.monotonic() - offline_started < 61:
        state = await restart_state()
        assert state["active"] == 1 and state["guard"]["error"] is None
        assert state["guard"]["claimed_at_ms"] is None and state["guard"]["settled_at_ms"] is None
        assert state["lastActivityAt"] == offline["lastActivityAt"]
        await asyncio.sleep(1)
    offline_observed_ms = round((time.monotonic() - offline_started) * 1000)
    await control(next_backend, "/test/restart-reconnect")
    recovered = await restart_state()
    assert recovered["active"] == 1 and recovered["lastActivityAt"] == offline["lastActivityAt"]
    await asyncio.sleep(1.2)
    repeated = await restart_state()
    assert repeated["active"] == 1 and repeated["lastActivityAt"] == recovered["lastActivityAt"]
    assert len((await snapshot(next_backend))["dispatches"]) == 1
    await control(next_backend, "/test/restart-drain", {"enabled": False})
    with pytest.raises(aiohttp.WSServerHandshakeError) as uncertain_voice:
        await session.ws_connect(
            started["gateway_url"].replace("http://", "ws://") + "/v1/listen",
            headers={"Authorization": f"Bearer {data['robots'][0]['robot_token']}"},
        )
    assert uncertain_voice.value.status == 503
    captured = await snapshot(next_backend)
    assert any(item["request_id"] == request_id for item in captured["cancellations"])
    assert len(captured["dispatches"]) == 1
    still_active = await restart_state()
    assert still_active["active"] == 1 and still_active["guard"]["claimed_at_ms"] is None
    await control(next_backend, "/test/control", {"robot": robot, "executing": False, "busy": False})

    async def idle_again():
        return (await restart_state())["active"] == 0

    await wait_until(idle_again)
    assert "Invented answer" in speech(await voice(restarted_backend, "ask Home Assistant to say fixture greeting"))
    assert len(selected.calls) == 1 and len((await snapshot(next_backend))["dispatches"]) == 1
    await wait_until(idle_again)
    last_activity = (await restart_state())["lastActivityAt"]

    async def guarded():
        state = await restart_state()
        assert state["guard"]["error"] is None
        return state["guard"]["settled_at_ms"] is not None

    await wait_until(guarded, seconds=80, interval=0.25)
    finished = await restart_state()
    assert finished["guard"]["claimed_at_ms"] - last_activity >= 60000
    assert finished["active"] == 0 and len((await snapshot(next_backend))["dispatches"]) == 1
    print(
        json.dumps(
            {
                "observed_production_guard_quiet_ms": finished["guard"]["claimed_at_ms"] - last_activity,
                "ha_version": HA_VERSION,
                "case": "native_restart_recovery",
                "gateway_process_ids": [started["pid"], replaced["pid"]],
                "native_offline_guard_observed_ms": offline_observed_ms,
                "replacement_startup_activity_counts": replaced["startup_activity_counts"],
                "native_announcement_frames": 1,
                "physical_robot": False,
                "production_service_restart": False,
            }
        )
    )


async def test_actual_jev_sdk_colors_scenes_scripts_rooms_and_local_query(hass, next_backend, monkeypatch):
    project = os.environ.get("JEV_PROJECT_DIR")
    if not project:
        pytest.skip("Set JEV_PROJECT_DIR to validate the Jev fork")
    import httpx2

    kitchen, bedroom, _ = await release_devices(hass)
    handoff = RecordingAgent()
    await hass.data["conversation"].async_add_entities([handoff])
    requests = []

    async def classifier(request):
        payload = json.loads(request.content)
        requests.append(payload)
        utterance = payload.get("state", {}).get("utterance", "")
        labels = {
            "category": "command",
            "domain": "light",
            "action": "turn_on",
            "scope": "named_area",
            "target_area": "Kitchen",
        }
        if "blue" in utterance:
            labels["action"] = "set_color"
        elif "percent" in utterance:
            labels["action"] = "set_brightness"
        elif "dinner" in utterance:
            labels.update(domain="scene", action="activate", scope="named_entity", target_area="none")
        elif "relax" in utterance:
            labels.update(domain="script", action="activate", scope="named_entity", target_area="none")
        elif "here" in utterance:
            labels.update(scope="unspecified", target_area="none")
        elif "ceiling" in utterance.lower() or "imaginary" in utterance.lower():
            labels.update(scope="named_entity", target_area="none")
        for key, question in payload["questions"].items():
            if question["type"] == "choice":
                assert labels[key] in question["criteria"], (key, labels[key], question["criteria"])
        answers = {
            key: {
                "type": "choice",
                "choice": labels[key],
                "confidence": 0.95,
                "probabilities": {
                    choice: 0.95 if choice == labels[key] else 0.05 / max(1, len(question["criteria"]) - 1)
                    for choice in question["criteria"]
                },
            }
            if question["type"] == "choice"
            else {"type": "noul", "noul": 0.05}
            for key, question in payload["questions"].items()
        }
        return httpx2.Response(
            200,
            json={
                "model": "typesafe/jev-latest",
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.00001},
            },
        )

    monkeypatch.setattr(
        "custom_components.jev_assist.jev_client._new_http_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(classifier), trust_env=False),
    )
    configured = await hass.config_entries.flow.async_init(
        "jev_assist",
        context={"source": "user"},
        data={"provider": "openrouter", "api_key": "invented-openrouter-key"},
    )
    assert configured["type"] == "create_entry", configured
    jev_entry = configured["result"]
    await hass.async_block_till_done()
    agent_id = er.async_get(hass).async_get_entity_id(
        "conversation", "jev_assist", jev_entry.entry_id + "-conversation"
    )
    assert agent_id
    from tests.test_agents import choose_agent

    await choose_agent(hass, jev_entry, handoff.entity_id, key="grok_handoff_agent_id")
    entry = await next_entry(hass, next_backend, monkeypatch)
    await options(hass, entry, conversation_agent=agent_id)
    assign_room(hass, entry, "Kitchen")
    for utterance in [
        "set kitchen light to blue",
        "set kitchen light brightness to fifty percent",
        "activate dinner scene",
        "run relax script",
        "turn on the lights here",
    ]:
        assert "couldn't" not in speech(await voice(next_backend, utterance))
    assert any(call[1].get("rgb_color") == (0, 0, 255) for call in kitchen.calls)
    assert any(call[1].get("brightness") == 128 for call in kitchen.calls)
    assert len(bedroom.calls) == 1, "Only the explicitly named script may control the other room"
    assert handoff.calls == []
    assert "couldn't" not in speech(await voice(next_backend, "turn it off"))
    assert kitchen.calls[-1][0] == "off" and len(bedroom.calls) == 1
    study_ceiling = SyntheticLight("Study Ceiling", "invented-study-ceiling")
    bedroom_ceiling = SyntheticLight("Bedroom Ceiling", "invented-bedroom-ceiling")
    await hass.data["light"].async_add_entities([study_ceiling, bedroom_ceiling])
    for light in (study_ceiling, bedroom_ceiling):
        exposed_entities.async_expose_entity(hass, "conversation", light.entity_id, True)
    await voice(next_backend, "ask Home Assistant to turn on Study Ceiling")
    assert len(study_ceiling.calls) == 1 and bedroom_ceiling.calls == []
    before_unknown = [len(light.calls) for light in (kitchen, bedroom, study_ceiling, bedroom_ceiling)]
    await voice(next_backend, "turn on Imaginary light")
    assert [len(light.calls) for light in (kitchen, bedroom, study_ceiling, bedroom_ceiling)] == before_unknown
    assert handoff.calls == []
    requests_before = len(requests)
    answer = await conversation.async_converse(
        hass,
        text="are kitchen lights on",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent_id,
        device_id=robot_context(entry).device_id,
    )
    assert answer.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    assert "off" in answer.response.speech["plain"]["speech"].lower()
    assert len(requests) == requests_before and handoff.calls == []
    exposed_entities.async_expose_entity(hass, "conversation", "scene.dinner", False)
    before = len(kitchen.calls)
    await voice(next_backend, "activate dinner scene")
    assert len(kitchen.calls) == before and handoff.calls == []
