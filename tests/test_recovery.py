"""Owner recovery guidance through actual isolated HA and pinned TLS sessions."""

import asyncio
import errno
import json
from pathlib import Path
from uuid import uuid4

import atomicwrites
import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.phoenix import diagnostics, recovery
from tests.direct_backend import SyntheticLocalRobot, linked_entry, wait_for
from tests.test_conversation import install_devices
from tests.test_pairing import make_entry


def issue(hass, entry, reason):
    return ir.async_get(hass).async_get_issue("phoenix", f"{entry.entry_id}_{reason}")


async def test_sustained_network_outage_clears_only_after_verified_reconnect_without_replay(hass, robot, monkeypatch):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    original = dict(entry.data)
    client = entry.runtime_data.client
    request_id = str(uuid4())
    result, _ = await robot.command("turn on kitchen light", request_id=request_id)
    assert result["outcome"] == "success" and len(kitchen.calls) == 1
    monkeypatch.setattr(recovery, "DISCONNECT_GRACE_SECONDS", 0.05)
    robot.redirect = f"https://{robot.host}:{robot.port}/unreachable"
    await robot.disconnect()
    await wait_for(lambda: issue(hass, entry, "connection_unavailable"))
    problem = issue(hass, entry, "connection_unavailable")
    assert problem.translation_key == "connection_unavailable" and not problem.is_persistent
    assert problem.translation_placeholders == {"name": entry.title}
    exported = json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
    for private in (entry.data["credential"], entry.data["fingerprint"], robot.robot_id, robot.host, "kitchen"):
        assert private not in exported
    assert "connection_unavailable" in exported
    robot.redirect = None
    await wait_for(lambda: client.ready and client.state == "connected")
    assert issue(hass, entry, "connection_unavailable") is None
    assert dict(entry.data) == original and len(kitchen.calls) == 1
    repeated, _ = await robot.command("turn on kitchen light", request_id=request_id)
    assert repeated["code"] == "duplicate_not_replayed" and len(kitchen.calls) == 1


async def test_brief_network_interruption_never_raises_a_repair(hass, robot, monkeypatch):
    monkeypatch.setattr(recovery, "DISCONNECT_GRACE_SECONDS", 5)
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    connected = robot.connections
    await robot.disconnect()
    await wait_for(lambda: not client.ready)
    assert not client.recovery.active
    await wait_for(lambda: client.ready and robot.connections > connected)
    assert not client.recovery.active and client.recovery._disconnect_cancel is None


async def test_retry_attempts_keep_original_five_minute_deadline_and_cancel_late_callback(hass, monkeypatch):
    """Capture HA's timer to check 300 seconds without sleeping five minutes."""
    scheduled = []
    cancelled = []

    def timer(_hass, delay, callback):
        scheduled.append((delay, callback))
        return lambda: cancelled.append(callback)

    monkeypatch.setattr(recovery, "async_call_later", timer)
    entry = make_entry({})
    monitor = recovery.PhoenixRecovery(hass, entry)
    monitor.state_changed("connecting", None)
    assert scheduled == []
    for _ in range(8):
        monitor.state_changed("disconnected", "cannot_connect")
        monitor.state_changed("connecting", None)
    assert len(scheduled) == 1 and scheduled[0][0] == 300
    assert issue(hass, entry, "connection_unavailable") is None
    scheduled[0][1](None)
    assert issue(hass, entry, "connection_unavailable") is not None
    monitor.state_changed("disconnected", "cannot_connect")
    assert len(scheduled) == 1
    monitor.state_changed("connected", None)
    assert issue(hass, entry, "connection_unavailable") is None
    monitor.state_changed("disconnected", "cannot_connect")
    monitor.stop()
    assert cancelled == [scheduled[1][1]]
    scheduled[1][1](None)
    assert not monitor.active and issue(hass, entry, "connection_unavailable") is None


async def test_unload_cancels_pending_network_repair(hass, robot, monkeypatch):
    monkeypatch.setattr(recovery, "DISCONNECT_GRACE_SECONDS", 0.15)
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    robot.redirect = f"https://{robot.host}:{robot.port}/unreachable"
    await robot.disconnect()
    await wait_for(lambda: client.recovery._disconnect_cancel is not None)
    await hass.config_entries.async_unload(entry.entry_id)
    await asyncio.sleep(0.2)
    assert not client.recovery.active and client.recovery._disconnect_cancel is None
    assert issue(hass, entry, "connection_unavailable") is None


@pytest.mark.parametrize("code,reason", [(4002, "connection_replaced"), (4003, "unsupported_protocol")])
async def test_terminal_peer_close_explains_failure_without_reconnection_loop(hass, robot, code, reason):
    entry = await linked_entry(hass, robot)
    original = dict(entry.data)
    client = entry.runtime_data.client
    connections = robot.connections
    await robot.disconnect(code)
    await wait_for(lambda: issue(hass, entry, reason))
    await asyncio.sleep(0.05)
    assert not client.ready and client.state == "protocol_error"
    assert robot.connections == connections and dict(entry.data) == original
    assert client.recovery._disconnect_cancel is None
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not client and entry.runtime_data.client.ready)
    assert issue(hass, entry, reason) is None


async def test_unverified_welcome_raises_repair_without_accepting_commands(hass, robot, monkeypatch):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    await hass.config_entries.async_unload(entry.entry_id)
    original = robot.send

    async def wrong_robot(frame):
        if frame["type"] == "welcome":
            frame = {**frame, "robot_id": str(uuid4())}
        await original(frame)

    monkeypatch.setattr(robot, "send", wrong_robot)
    robot.frames.clear()
    await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: issue(hass, entry, "invalid_peer"))
    assert not entry.runtime_data.client.ready and not kitchen.calls and not robot.frames


async def test_authentication_failure_uses_existing_reauth_and_cancels_outage_timer(hass, robot, monkeypatch):
    monkeypatch.setattr(recovery, "DISCONNECT_GRACE_SECONDS", 0.1)
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    client.recovery.state_changed("disconnected", "cannot_connect")
    await robot.disconnect(4001)
    await wait_for(lambda: client.state == "authentication_required")
    await asyncio.sleep(0.15)
    assert not client.recovery.active and client.recovery._disconnect_cancel is None
    assert any(
        flow["context"].get("source") == "reauth" and flow["context"].get("entry_id") == entry.entry_id
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_startup_storage_read_failure_preserves_ledger_and_never_connects(hass, robot):
    entry = await linked_entry(hass, robot)
    path = Path(entry.runtime_data.client.storage.path)
    await hass.config_entries.async_unload(entry.entry_id)
    path.write_text("{invented corrupt ledger")
    connections = robot.connections
    await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: issue(hass, entry, "request_storage"))
    client = entry.runtime_data.client
    assert client._storage_failed and not client.ready and robot.connections == connections
    assert path.read_text() == "{invented corrupt ledger"
    assert (await diagnostics.async_get_config_entry_diagnostics(hass, entry))["request_storage_failed"]


async def test_storage_write_failure_survives_reconnect_and_reload_until_durable_write_works(hass, robot, monkeypatch):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    client = entry.runtime_data.client
    path = Path(client.storage.path)
    original_ledger = path.read_bytes()
    original = atomicwrites._replace_atomic

    def full_disk(source, destination):
        if str(destination) == str(path):
            raise OSError(errno.ENOSPC, "Invented full disk")
        return original(source, destination)

    monkeypatch.setattr(atomicwrites, "_replace_atomic", full_disk)
    request_id = str(uuid4())
    result, _ = await robot.command("turn on kitchen light", request_id=request_id)
    assert result["code"] == "request_storage" and kitchen.calls == []
    assert issue(hass, entry, "request_storage")
    assert not any(frame["type"] == "accepted" and frame.get("request_id") == request_id for frame in robot.frames)
    await robot.disconnect()
    await wait_for(lambda: not client.ready)
    await wait_for(lambda: client.ready)
    assert issue(hass, entry, "request_storage") and client._storage_failed
    assert path.read_bytes() == original_ledger and not kitchen.calls
    connections = robot.connections
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not client and issue(hass, entry, "request_storage"))
    blocked = entry.runtime_data.client
    assert blocked._storage_failed and not blocked.ready and robot.connections == connections
    assert path.read_bytes() == original_ledger
    monkeypatch.setattr(atomicwrites, "_replace_atomic", original)
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client is not blocked and entry.runtime_data.client.ready)
    assert issue(hass, entry, "request_storage") is None and not kitchen.calls
    assert path.read_bytes() == original_ledger


async def test_removing_one_entry_preserves_other_robots_repair(hass, robot, tmp_path):
    entry = await linked_entry(hass, robot)
    other = await SyntheticLocalRobot(tmp_path / "another-invented-robot").start()
    try:
        other_entry = await linked_entry(hass, other)
        await robot.disconnect(4003)
        await other.disconnect(4003)
        await wait_for(
            lambda: issue(hass, entry, "unsupported_protocol") and issue(hass, other_entry, "unsupported_protocol")
        )
        await hass.config_entries.async_remove(entry.entry_id)
        assert issue(hass, entry, "unsupported_protocol") is None
        assert issue(hass, other_entry, "unsupported_protocol")
        assert other.credential is not None
    finally:
        await other.close()
