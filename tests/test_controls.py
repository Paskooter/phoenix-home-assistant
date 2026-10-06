"""Real isolated HA control platforms and invented authenticated TLS endpoints."""

import asyncio
import json
from pathlib import Path

import atomicwrites
import pytest
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix.const import DOMAIN
from custom_components.phoenix.controls import CONTROL_OPTIONS
from custom_components.phoenix.controls_api import async_upload_media
from tests.control_backend import SyntheticControlRobot, invented_image, invented_wav
from tests.direct_backend import linked_entry, wait_for
from tests.test_agents import choose_agent


@pytest.fixture
async def control_robot(tmp_path):
    peer = await SyntheticControlRobot(tmp_path / "invented-control-robot").start()
    try:
        yield peer
    finally:
        await peer.close()


def entity(hass, entry, domain, key):
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.unique_id}_{key}")
    assert entity_id
    return hass.data[domain].get_entity(entity_id)


async def enable_controls(hass, entry, robot, groups=None):
    groups = set(CONTROL_OPTIONS) if groups is None else set(groups)
    old = entry.runtime_data.client
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            **{option: group in groups for group, option in CONTROL_OPTIONS.items()},
        },
    )
    await wait_for(
        lambda: (
            entry.runtime_data.client is not old
            and entry.runtime_data.client.ready
            and set(robot.enabled) == groups - robot.denied_groups
        )
    )
    await wait_for(lambda: entry.runtime_data.client.observed_control("installed_skills") is not None)
    await hass.async_block_till_done()
    return entry.runtime_data.client


async def local_media(hass, tmp_path):
    media_dir = tmp_path / "invented-media"
    media_dir.mkdir()
    hass.config.media_dirs = {"invented": str(media_dir)}
    assert await async_setup_component(hass, "media_source", {})
    (media_dir / "invented.png").write_bytes(invented_image())
    (media_dir / "invented.wav").write_bytes(invented_wav())
    return media_dir


async def test_unsupported_be_has_no_control_entities_and_telemetry_is_unchanged(hass, robot):
    entry = await linked_entry(hass, robot)
    assert "robot_controls" not in entry.runtime_data.client.capabilities
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert not any(
        item.domain in ("text", "light", "media_player", "select", "switch", "button", "camera") for item in entities
    )
    assert "controls_enabled" not in robot.preferences
    await robot.telemetry({"speaker_volume_percent": 35, "sleeping": False})
    await wait_for(lambda: entry.runtime_data.client.telemetry_values.get("speaker_volume_percent") == 35)
    assert entry.runtime_data.client.telemetry_values["sleeping"] is False


async def test_native_controls_are_default_off_and_require_permission_acknowledgement(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    await hass.async_block_till_done()
    client = entry.runtime_data.client
    for domain, key in (
        ("text", "screen_text"),
        ("light", "ring_light"),
        ("media_player", "speaker"),
        ("switch", "sleep_control"),
        ("select", "installed_skill"),
        ("camera", "camera"),
    ):
        assert entity(hass, entry, domain, key).available is False
    assert entity(hass, entry, "button", "stop_activity").available
    assert not er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.unique_id}_show_weather")
    assert control_robot.preferences["controls_enabled"] == []
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "display_text", {"text": "Invented message"})
    assert not control_robot.control_calls
    control_robot.denied_groups.add("screen")
    client = await enable_controls(hass, entry, control_robot, ["screen"])
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "display_text", {"text": "Invented unapproved message"})
    assert not control_robot.control_calls and not entity(hass, entry, "text", "screen_text").available


async def test_options_expose_independent_default_off_permissions_and_preserve_them(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    form = flow["data_schema"]({"conversation_agent": conversation.HOME_ASSISTANT_AGENT})
    assert all(form[key] is False for key in CONTROL_OPTIONS.values())
    hass.config_entries.options.async_abort(flow["flow_id"])
    await choose_agent(hass, entry, conversation.HOME_ASSISTANT_AGENT, allow_screen=True, allow_ring_light=True)
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert entry.options["allow_screen"] and entry.options["allow_ring_light"]
    assert all(
        entry.options[key] is False
        for key in CONTROL_OPTIONS.values()
        if key not in ("allow_screen", "allow_ring_light")
    )
    await choose_agent(hass, entry, conversation.HOME_ASSISTANT_AGENT, allow_ring_light=False)
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert entry.options["allow_screen"] and entry.options["allow_ring_light"] is False


async def test_native_platform_services_report_only_observed_state(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    await enable_controls(hass, entry, control_robot)
    screen = entity(hass, entry, "text", "screen_text")
    ring = entity(hass, entry, "light", "ring_light")
    speaker = entity(hass, entry, "media_player", "speaker")
    sleep = entity(hass, entry, "switch", "sleep_control")
    skills = entity(hass, entry, "select", "installed_skill")
    await hass.services.async_call(
        "text", "set_value", {"entity_id": screen.entity_id, "value": "Invented hello"}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ring.entity_id, "rgb_color": [0, 128, 255], "brightness": 128}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": speaker.entity_id, "volume_level": 0.33}, blocking=True
    )
    await hass.services.async_call("switch", "turn_on", {"entity_id": sleep.entity_id}, blocking=True)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": skills.entity_id, "option": "Invented Clock"}, blocking=True
    )
    assert screen.native_value == "Invented hello"
    assert ring.is_on is True and ring.brightness == 128 and ring.rgb_color == (0, 128, 255)
    assert speaker.volume_level == 0.33 and sleep.is_on is True and skills.current_option == "Invented Clock"
    assert [frame["action"] for frame in control_robot.control_calls] == [
        "display_text",
        "set_ring_color",
        "set_volume",
        "sleep",
        "run_skill",
    ]
    assert all(len(json.dumps(frame).encode()) <= 8192 for frame in control_robot.control_calls)
    control_robot.publish_state = False
    await speaker.async_set_volume_level(0.7)
    assert speaker.volume_level == 0.33  # A confirmed action is not an invented state observation.
    await control_robot.controls_state()
    await wait_for(lambda: speaker.volume_level == 0.7)
    await screen.async_clear_screen()
    assert screen.native_value == "Invented hello"
    await control_robot.controls_state()
    await wait_for(lambda: screen.native_value is None)


async def test_pinned_bounded_local_media_supports_images_audio_and_playback(hass, control_robot, tmp_path):
    await local_media(hass, tmp_path)
    entry = await linked_entry(hass, control_robot)
    await enable_controls(hass, entry, control_robot, ["screen", "audio"])
    screen = entity(hass, entry, "text", "screen_text")
    speaker = entity(hass, entry, "media_player", "speaker")
    await hass.services.async_call(
        "phoenix",
        "show_image",
        {
            "entity_id": screen.entity_id,
            "media_content_id": "media-source://media_source/invented/invented.png",
            "duration_ms": 2000,
        },
        blocking=True,
    )
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": speaker.entity_id,
            "media_content_type": "audio/wav",
            "media_content_id": "media-source://media_source/invented/invented.wav",
        },
        blocking=True,
    )
    assert screen.observed("screen_image_id") == control_robot.uploads[0][0]
    assert speaker.state == "playing"
    assert [upload[1] for upload in control_robot.uploads] == ["image/png", "audio/wav"]
    assert control_robot.uploads[0][2] == invented_image() and control_robot.uploads[1][2] == invented_wav()
    assert not any("media-source://" in json.dumps(frame) for frame in control_robot.control_calls)
    await hass.services.async_call("media_player", "media_pause", {"entity_id": speaker.entity_id}, blocking=True)
    assert speaker.state == "paused"
    await hass.services.async_call("media_player", "media_play", {"entity_id": speaker.entity_id}, blocking=True)
    assert speaker.state == "playing"
    await hass.services.async_call("media_player", "media_stop", {"entity_id": speaker.entity_id}, blocking=True)
    assert speaker.state == "idle" and speaker.media_content_id is None


async def test_media_rejects_remote_urls_wrong_types_oversize_symlinks_and_redirects(hass, control_robot, tmp_path):
    media_dir = await local_media(hass, tmp_path)
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot, ["screen", "audio"])
    (media_dir / "oversize.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 1024 * 1024)
    (media_dir / "wrong.png").write_bytes(b"Invented non-image")
    outside = tmp_path / "outside.png"
    outside.write_bytes(invented_image())
    (media_dir / "escape.png").symlink_to(outside)
    for source in (
        "http://127.0.0.1/private",
        "file:///private.png",
        "media-source://other/invented.png",
        "media-source://media_source/invented/oversize.png",
        "media-source://media_source/invented/wrong.png",
        "media-source://media_source/invented/escape.png",
        "media-source://media_source/invented/invented.wav",
    ):
        with pytest.raises(ServiceValidationError):
            await async_upload_media(client, control_robot.robot_id, source, "image", "text.invented_screen")
    assert not control_robot.uploads and not control_robot.control_calls
    control_robot.upload_redirect = "https://127.0.0.1:1/invented-leak"
    with pytest.raises(ServiceValidationError):
        await async_upload_media(
            client,
            control_robot.robot_id,
            "media-source://media_source/invented/invented.png",
            "image",
            "text.invented_screen",
        )
    assert not control_robot.uploads and not control_robot.control_calls


async def test_camera_reads_require_explicit_start_and_native_privacy_guard(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot, ["camera"])
    camera = entity(hass, entry, "camera", "camera")
    assert camera.is_on is False
    with pytest.raises(ServiceValidationError):
        await camera.async_camera_image()
    assert control_robot.snapshot_calls == 0 and not control_robot.control_calls
    await hass.services.async_call("camera", "turn_on", {"entity_id": camera.entity_id}, blocking=True)
    assert camera.is_on and camera.is_streaming
    image = await camera.async_camera_image()
    assert image.startswith(b"\xff\xd8\xff") and control_robot.snapshot_calls == 1
    control_robot.hatch_closed = False
    with pytest.raises(ServiceValidationError):
        await camera.async_camera_image()
    await camera.async_turn_off()
    assert camera.is_on is False
    with pytest.raises(ServiceValidationError):
        await camera.async_turn_on()
    assert client.observed_control("camera_active") is False
    control_robot.hatch_closed = True
    await client.async_control(control_robot.robot_id, "start_camera", {"duration_ms": 150})
    await wait_for(lambda: client.observed_control("camera_active") is None)
    assert not camera.available


async def test_busy_touch_quiet_hours_and_fresh_catalog_block_new_actions(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot)
    control_robot.busy = True
    await control_robot.roster()
    await wait_for(lambda: client.robots[control_robot.robot_id].busy)
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "display_text", {"text": "Invented busy message"})
    control_robot.busy = False
    await control_robot.roster()
    await wait_for(lambda: not client.robots[control_robot.robot_id].busy)
    await control_robot.telemetry({"head_touch": True})
    await wait_for(lambda: client.telemetry_values.get("head_touch") is True)
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "set_ring_color", {"rgb": [20, 40, 60]})
    await control_robot.telemetry({"head_touch": False})
    await wait_for(lambda: client.telemetry_values.get("head_touch") is False)
    old = client
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "quiet_hours_enabled": True,
            "quiet_hours_start": "00:00:00",
            "quiet_hours_end": "00:00:00",
        },
    )
    await wait_for(lambda: entry.runtime_data.client is not old and entry.runtime_data.client.ready)
    client = entry.runtime_data.client
    await wait_for(lambda: client.observed_control("installed_skills") is not None)
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "run_skill", {"skill_id": "@invented/clock"})
    await client.async_control(control_robot.robot_id, "display_text", {"text": "Invented quiet display"})
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "run_skill", {"skill_id": "@invented/arbitrary"})
    assert len(control_robot.control_calls) == 1


async def test_cleanup_stop_works_with_busy_pending_and_default_off_permissions(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot, ["screen"])
    control_robot.hold_actions.add("display_text")
    pending = asyncio.create_task(
        client.async_control(control_robot.robot_id, "display_text", {"text": "Invented held display"})
    )
    await wait_for(lambda: bool(control_robot.held))
    control_robot.busy = True
    await control_robot.roster()
    await wait_for(lambda: client.robots[control_robot.robot_id].busy)
    # Skills opt-in is false. Cleanup nevertheless releases only HA-owned work.
    stop = entity(hass, entry, "button", "stop_activity")
    await hass.services.async_call("button", "press", {"entity_id": stop.entity_id}, blocking=True)
    with pytest.raises(HomeAssistantError) as error:
        await pending
    assert error.value.translation_key == "control_confirmation_lost"
    assert [frame["action"] for frame in control_robot.control_calls] == ["display_text", "stop"]
    assert not client.pending_actions


async def test_disconnect_never_replays_and_unload_clears_control_timers(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot, ["screen"])
    control_robot.hold_actions.add("display_text")
    task = asyncio.create_task(
        client.async_control(control_robot.robot_id, "display_text", {"text": "Invented uncertain display"})
    )
    await wait_for(lambda: bool(control_robot.held))
    await control_robot.disconnect()
    with pytest.raises(HomeAssistantError) as error:
        await task
    assert error.value.translation_key == "control_confirmation_lost"
    assert client.observed_control("screen_active") is None
    await wait_for(lambda: client.ready)
    assert len(control_robot.control_calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not client.pending_actions and client._control_timer is None and client._camera_timer is None
    assert client.control_values == {}


async def test_authenticated_name_updates_preserve_user_name_area_and_ids(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    await enable_controls(hass, entry, control_robot, ["screen"])
    screen = entity(hass, entry, "text", "screen_text")
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, control_robot.robot_id), entry.entry_id)
    device_id, entity_id = device.id, screen.entity_id
    credential, generation = entry.data["credential"], entry.data["generation"]
    assert device.sw_version == "13.3.0"
    area = ar.async_get(hass).async_create("Invented Lab")
    registry.async_update_device(device_id, name_by_user="Invented owner label", area_id=area.id)
    control_robot.name = "Invented stored nickname"
    await control_robot.roster()
    await wait_for(lambda: registry.async_get(device_id).name == "Invented stored nickname")
    assert registry.async_get(device_id).name_by_user == "Invented owner label"
    control_robot.name = "Amber Quiet River Finch"
    control_robot.firmware_version = "13.3.1"
    await control_robot.roster()
    await wait_for(lambda: registry.async_get(device_id).name == "Amber Quiet River Finch")
    assert registry.async_get(device_id).name_by_user == "Invented owner label"
    previous = entry.runtime_data.client
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.ready)
    assert entity(hass, entry, "text", "screen_text").entity_id == entity_id
    device = registry.async_get(device_id)
    assert device.name == "Amber Quiet River Finch" and device.name_by_user == "Invented owner label"
    assert device.sw_version == "13.3.1"
    assert device.area_id == area.id
    assert entry.data["credential"] == credential and entry.data["generation"] == generation


async def test_missing_malformed_stale_native_states_never_become_idle_or_off(hass, control_robot):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot)
    ring = entity(hass, entry, "light", "ring_light")
    speaker = entity(hass, entry, "media_player", "speaker")
    sleep = entity(hass, entry, "switch", "sleep_control")
    await control_robot.controls_state(
        values={
            "ring_rgb": [999, 0, 0],
            "speaker_volume_percent": float("nan"),
            "sleeping": 0,
            "audio_state": "invented",
        }
    )
    await wait_for(lambda: client.observed_control("speaker_volume_percent") is None)
    assert ring.rgb_color is None and ring.is_on is None and speaker.state is None and sleep.is_on is None
    old = client
    control_robot.initial_state_age_ms = 59_750
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not old and entry.runtime_data.client.ready)
    client = entry.runtime_data.client
    ring = entity(hass, entry, "light", "ring_light")
    speaker = entity(hass, entry, "media_player", "speaker")
    sleep = entity(hass, entry, "switch", "sleep_control")
    await wait_for(lambda: speaker.volume_level == 0.42)
    await wait_for(lambda: client.control_received_at is None)
    assert ring.is_on is None and speaker.volume_level is None and sleep.is_on is None
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "run_skill", {"skill_id": "@invented/clock"})
    assert not control_robot.control_calls


async def test_control_admission_requires_real_durable_write_before_dispatch(hass, control_robot, monkeypatch):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot, ["screen"])
    real_replace = atomicwrites._replace_atomic

    def disk_full(source, destination):
        if str(destination) == client.storage.path:
            raise OSError(28, "Invented full disk")
        return real_replace(source, destination)

    monkeypatch.setattr(atomicwrites, "_replace_atomic", disk_full)
    with pytest.raises(ServiceValidationError):
        await client.async_control(control_robot.robot_id, "display_text", {"text": "Invented blocked display"})
    assert client._storage_failed and not client.pending_actions
    assert not control_robot.control_calls


async def test_touch_during_durable_admission_is_rechecked_without_actuation(hass, control_robot, monkeypatch):
    entry = await linked_entry(hass, control_robot)
    client = await enable_controls(hass, entry, control_robot, ["screen"])
    writing, release_write = asyncio.Event(), asyncio.Event()
    real_save = client.storage.async_save

    async def delayed_write(data):
        writing.set()
        await release_write.wait()
        await real_save(data)

    monkeypatch.setattr(client.storage, "async_save", delayed_write)
    pending = asyncio.create_task(
        client.async_control(control_robot.robot_id, "display_text", {"text": "Invented admission race"})
    )
    await wait_for(writing.is_set)
    await control_robot.telemetry({"head_touch": True})
    await wait_for(lambda: client.telemetry_values.get("head_touch") is True)
    release_write.set()
    with pytest.raises(ServiceValidationError):
        await pending
    assert not control_robot.control_calls and not client.pending_actions
    ledger = Path(client.storage.path).read_text()
    assert "control/" in ledger and "Invented admission race" not in ledger and "text" not in ledger
    assert await client.storage.async_load() == client.seen
