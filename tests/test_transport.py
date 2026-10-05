"""Direct TLS lifecycle, strict durable admission, deadlines and no replay."""

import asyncio
import errno
import json
import os
from pathlib import Path
from uuid import uuid4

import atomicwrites
import pytest
from aiohttp import web
from homeassistant.exceptions import ServiceValidationError

from custom_components.phoenix import diagnostics
from tests.direct_backend import SyntheticLocalRobot, linked_entry, wait_for
from tests.test_conversation import install_devices


async def allow_announcements(hass, entry, robot):
    previous = entry.runtime_data.client
    hass.config_entries.async_update_entry(entry, options={**entry.options, "allow_announcements": True})
    await wait_for(
        lambda: (
            entry.runtime_data.client is not previous
            and entry.runtime_data.client.ready
            and robot.announcements_enabled
        )
    )
    return entry.runtime_data.client


async def test_tls_flow_builtin_results_reload_unload_remove(hass, robot):
    kitchen, bedroom = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    for text in [
        "turn on kitchen lights",
        "turn off kitchen lights",
        "set bedroom light brightness to fifty percent",
        "set bedroom light to blue",
    ]:
        result, _ = await robot.command(text)
        assert result["outcome"] == "success", (text, result)
    assert [call[0] for call in kitchen.calls] == ["on", "off"]
    assert len(bedroom.calls) == 2 and any(call[1].get("brightness") == 128 for call in bedroom.calls)
    exported = json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
    for private in (entry.data["credential"], entry.data["fingerprint"], robot.robot_id, "kitchen", "turn on"):
        assert private not in exported
    previous = entry.runtime_data.client
    count = len(kitchen.calls) + len(bedroom.calls)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.ready)
    assert previous.stopping and previous.socket is None and previous.session.closed
    assert count == len(kitchen.calls) + len(bedroom.calls)
    client = entry.runtime_data.client
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client.stopping and client.socket is None and not client.commands and client.session.closed
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.ready)
    ledger = Path(entry.runtime_data.client.storage.path)
    await hass.config_entries.async_remove(entry.entry_id)
    assert robot.credential is None and robot.direct_enabled and not ledger.exists()
    assert count == len(kitchen.calls) + len(bedroom.calls)


async def test_duplicate_and_restart_never_replay_action(hass, robot):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    request_id = str(uuid4())
    result, _ = await robot.command("turn on kitchen lights", request_id=request_id)
    assert result["outcome"] == "success" and len(kitchen.calls) == 1
    same, _ = await robot.command("turn on kitchen lights", request_id=request_id)
    assert same["outcome"] == "success" and len(kitchen.calls) == 1
    old = entry.runtime_data.client
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not old and entry.runtime_data.client.ready)
    duplicate, _ = await robot.command("turn on kitchen lights", request_id=request_id)
    assert duplicate["outcome"] == "uncertain" and duplicate["code"] == "duplicate_not_replayed"
    assert len(kitchen.calls) == 1 and entry.runtime_data.client.contexts == {}


async def test_expired_request_has_no_acknowledgment_or_service(hass, robot):
    kitchen, _ = await install_devices(hass)
    await linked_entry(hass, robot)
    request_id = str(uuid4())
    result, _ = await robot.command("turn on kitchen lights", request_id=request_id, deadline_seconds=-1)
    assert result["outcome"] == "expired" and kitchen.calls == []
    assert not any(frame["type"] == "accepted" and frame.get("request_id") == request_id for frame in robot.frames)


async def test_real_atomic_write_failure_blocks_ack_services_and_reverse_frames(hass, robot, monkeypatch):
    """The real HA atomic writer raises ENOSPC at commit, unlike Store's logging."""
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    client = await allow_announcements(hass, entry, robot)
    real_replace = atomicwrites._replace_atomic

    def disk_full(source, destination):
        if str(destination) == client.storage.path:
            raise OSError(errno.ENOSPC, "Invented full disk")
        return real_replace(source, destination)

    monkeypatch.setattr(atomicwrites, "_replace_atomic", disk_full)
    request_id = str(uuid4())
    result, _ = await robot.command("turn on kitchen light", request_id=request_id)
    assert result == {"outcome": "error", "response_type": "error", "speech": "", "code": "request_storage"}
    assert kitchen.calls == [] and client._storage_failed
    assert not any(frame["type"] == "accepted" and frame.get("request_id") == request_id for frame in robot.frames)
    with pytest.raises(ServiceValidationError) as error:
        await client.async_announce(robot.robot_id, "Invented announcement")
    assert error.value.translation_key == "request_storage"
    assert robot.speech_calls == [] and not any(frame["type"] == "robot_action" for frame in robot.frames)


async def test_reverse_atomic_write_failure_never_sends_action(hass, robot, monkeypatch):
    await install_devices(hass)
    entry = await linked_entry(hass, robot)
    client = await allow_announcements(hass, entry, robot)
    real_replace = atomicwrites._replace_atomic

    def disk_full(source, destination):
        if str(destination) == client.storage.path:
            raise OSError(errno.ENOSPC, "Invented full disk")
        return real_replace(source, destination)

    monkeypatch.setattr(atomicwrites, "_replace_atomic", disk_full)
    with pytest.raises(ServiceValidationError) as error:
        await client.async_announce(robot.robot_id, "Invented announcement")
    assert error.value.translation_key == "request_storage"
    assert client._storage_failed and not client.pending_actions
    assert robot.speech_calls == [] and not any(frame["type"] == "robot_action" for frame in robot.frames)


async def test_tombstone_file_is_fsynced_private_and_contains_no_text(hass, robot, monkeypatch):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    syncs = []
    original = atomicwrites._proper_fsync

    def synced(fd):
        syncs.append(os.readlink(f"/proc/self/fd/{fd}"))
        return original(fd)

    monkeypatch.setattr(atomicwrites, "_proper_fsync", synced)
    result, _ = await robot.command("turn on kitchen light")
    assert result["outcome"] == "success" and len(kitchen.calls) == 1
    path = Path(entry.runtime_data.client.storage.path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert any(str(path.parent) in target for target in syncs)
    assert str(path.parent) in syncs  # file and containing directory durability
    content = path.read_text()
    assert "turn on" not in content and "kitchen" not in content and entry.data["credential"] not in content
    assert len(json.loads(content)["requests"]) == 1


async def test_redirect_never_forwards_key_to_another_origin(hass, robot):
    entry = await linked_entry(hass, robot)
    reached = []

    async def insecure(request):
        reached.append(request.headers.get("Authorization"))
        return web.Response(text="Must not be reached")

    app = web.Application()
    app.router.add_get("/target", insecure)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        await hass.config_entries.async_unload(entry.entry_id)
        robot.redirect = f"http://127.0.0.1:{port}/target"
        await hass.config_entries.async_setup(entry.entry_id)
        await wait_for(lambda: entry.runtime_data.client.last_error == "cannot_connect")
        assert reached == [] and not entry.runtime_data.client.ready
    finally:
        await runner.cleanup()


async def test_changed_pin_runtime_sends_no_credential_and_never_cloud_falls_back(hass, robot, tmp_path):
    entry = await linked_entry(hass, robot)
    await hass.config_entries.async_unload(entry.entry_id)
    other = await SyntheticLocalRobot(tmp_path / "other-peer").start()
    try:
        hass.config_entries.async_update_entry(entry, data={**entry.data, "port": other.port})
        await hass.config_entries.async_setup(entry.entry_id)
        await wait_for(lambda: entry.runtime_data.client.state == "authentication_required")
        assert entry.runtime_data.client.last_error == "certificate_changed"
        assert other.http_requests == [] and not entry.runtime_data.client.commands
        assert entry.data["credential"] == robot.credential and "phoenix_url" not in entry.data
    finally:
        await other.close()


async def test_silent_broken_peer_goes_offline_and_clears_telemetry(hass, robot):
    robot.auto_pong = False
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    await robot.telemetry({"battery_percent": 55})
    await wait_for(lambda: client.telemetry_available("battery_percent"))
    await wait_for(lambda: not client.ready, seconds=20)
    assert client.telemetry_values == {} and client.contexts == {}
    assert not client.commands


@pytest.mark.parametrize("field", ["robot_id", "generation", "session_id"])
async def test_wrong_bound_identity_never_acknowledges_or_executes(hass, robot, field):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    request_id = str(uuid4())
    frame = {
        "type": "command",
        "request_id": request_id,
        "robot_id": robot.robot_id,
        "language": "en",
        "text": "turn on kitchen light",
        "deadline_ms": robot.now_ms() + 7500,
    }
    frame[field] = robot.generation + 1 if field == "generation" else str(uuid4())
    await robot.send(frame)
    await wait_for(lambda: not entry.runtime_data.client.ready)
    assert not kitchen.calls and not entry.runtime_data.client.seen
    assert not any(frame["type"] == "accepted" and frame.get("request_id") == request_id for frame in robot.frames)


@pytest.mark.parametrize("lost_state", ["unavailable", "unknown", "missing", "later_missing"])
async def test_builtin_postdispatch_lost_state_never_confirms_or_retries(hass, robot, monkeypatch, lost_state):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)

    async def lost(**kwargs):
        kitchen.calls.append(("on", kwargs))
        if lost_state == "later_missing":
            # HA can return success before a native device confirms its state.
            asyncio.get_running_loop().call_later(0.35, hass.states.async_remove, kitchen.entity_id)
        elif lost_state == "missing":
            hass.states.async_remove(kitchen.entity_id)
        else:
            hass.states.async_set(kitchen.entity_id, lost_state)

    monkeypatch.setattr(kitchen, "async_turn_on", lost)
    result, latency = await robot.command("turn on kitchen light")
    assert result["outcome"] == "uncertain", result
    assert len(kitchen.calls) == 1 and entry.runtime_data.client.contexts == {}
    if lost_state == "later_missing":
        assert result["code"] == "confirmation_lost" and 7250 <= latency <= 8500
    else:
        assert result["success_count"] == 0 and result["failed_count"] == 1
    await asyncio.sleep(0.05)
    assert len(kitchen.calls) == 1
