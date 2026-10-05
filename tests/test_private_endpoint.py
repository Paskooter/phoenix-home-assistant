"""Optional mandatory-local release gate: real private endpoint on actual Node6.

Public CI runs the independent Python TLS fixtures. Set both environment paths
locally; this file never retrieves, copies or publishes the private module.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from homeassistant.components import conversation

from tests.direct_backend import wait_for
from tests.test_agents import choose_agent
from tests.test_conversation import install_devices
from tests.test_telemetry import VALUES, metric_id


class NativePeer:
    def __init__(self, process, data):
        self.process, self.data = process, data
        self.host, self.port, self.robot_id = data["host"], data["port"], data["robot_id"]
        self.sequence = 0
        self.pending = {}
        self.announcements_enabled = False
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        while line := await self.process.stdout.readline():
            data = json.loads(line)
            if future := self.pending.get(data.get("id")):
                if data.get("error"):
                    future.set_exception(AssertionError(data["error"]))
                else:
                    future.set_result(data.get("result"))

    async def rpc(self, op, **kwargs):
        self.sequence += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.sequence] = future
        self.process.stdin.write((json.dumps({"id": self.sequence, "op": op, **kwargs}) + "\n").encode())
        await self.process.stdin.drain()
        try:
            async with asyncio.timeout(12):
                return await future
        finally:
            self.pending.pop(self.sequence, None)

    async def linked_entry(self, hass):
        await self.rpc("open")
        form = await hass.config_entries.flow.async_init(
            "phoenix", context={"source": "user"}, data={"host": self.host, "port": self.port}
        )
        assert form["step_id"] == "pair_confirm", form
        state = await self.rpc("status")
        assert form["description_placeholders"]["code"] == state["sas"]
        assert not state["paired"]
        assert await self.rpc("approve")
        created = await hass.config_entries.flow.async_configure(form["flow_id"], {"confirm_pairing": True})
        assert created["type"] == "create_entry", created
        entry = created["result"]
        await wait_for(lambda: entry.runtime_data.client.ready)
        return entry


@pytest.fixture
async def native_peer(tmp_path):
    module = os.environ.get("PHOENIX_DIRECT_MODULE")
    binary = os.environ.get("PHOENIX_DIRECT_NODE_BINARY")
    if not module or not binary:
        pytest.skip("Private release gate needs PHOENIX_DIRECT_MODULE and PHOENIX_DIRECT_NODE_BINARY")
    directory = tmp_path / "private-invented-robot"
    helper = Path(__file__).with_name("direct_node_backend.cjs")
    seed = await asyncio.create_subprocess_exec(
        "node",
        str(helper),
        "--seed",
        str(directory),
        env=os.environ,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(seed.communicate(), 15)
    assert seed.returncode == 0, stderr.decode()
    assert json.loads(stdout)["seeded"]
    process = await asyncio.create_subprocess_exec(
        binary,
        str(helper),
        "--serve",
        str(directory),
        env=os.environ,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
    )
    peer = None
    try:
        async with asyncio.timeout(15):
            data = json.loads(await process.stdout.readline())
        assert data["node_version"] == "v6.5.0", data
        if expected := os.environ.get("PHOENIX_DIRECT_MODULE_SHA256"):
            assert data["module_sha256"] == expected
        peer = NativePeer(process, data)
        yield peer
    finally:
        if peer is not None and process.returncode is None:
            await peer.rpc("close")
        if process.returncode is None:
            await asyncio.wait_for(process.wait(), 5)
        if peer:
            await peer.reader
        assert process.returncode == 0, (await process.stderr.read()).decode()


async def test_actual_node6_pair_command_query_relative_routine_and_reconnect(hass, native_peer):
    kitchen, _ = await install_devices(hass)
    entry = await native_peer.linked_entry(hass)
    result = await native_peer.rpc("command", text="turn on kitchen lights")
    assert result["outcome"] == "success" and len(kitchen.calls) == 1, result
    result = await native_peer.rpc("command", text="are the kitchen lights on", route={"kind": "query"})
    assert result["outcome"] == "success" and len(kitchen.calls) == 1
    # HA offers fresh scoped context; native checks it before dispatching.
    await wait_for(lambda: entry.runtime_data.client.contexts)
    await asyncio.sleep(0.05)
    result = await native_peer.rpc("command", text="make it dimmer", route={"kind": "follow_up"})
    assert result["outcome"] == "success" and kitchen.calls[-1][1]["brightness"] == 229, result
    await choose_agent(
        hass, entry, conversation.HOME_ASSISTANT_AGENT, shortcut_phrase="reading time", shortcut_target="scene.dinner"
    )
    await wait_for(lambda: entry.runtime_data.client.ready)
    await asyncio.sleep(0.05)
    shortcut = entry.options["routine_shortcuts"][0]
    result = await native_peer.rpc(
        "command", text="reading time", route={"kind": "routine", "shortcut_id": shortcut["id"]}
    )
    assert result["outcome"] == "success" and "started" in result["speech"], result
    assert hass.states.get("scene.dinner").state != "unknown"
    routine_calls = len(kitchen.calls)
    previous = entry.runtime_data.client
    await native_peer.rpc("disconnect")
    await wait_for(lambda: not previous.ready)
    await wait_for(lambda: previous.ready)
    assert len(kitchen.calls) == routine_calls and previous.contexts == {}
    declined = await native_peer.rpc("command", text="turn off kitchen lights", wake=False)
    assert declined["code"] == "wake_required" and len(kitchen.calls) == routine_calls
    # Actual endpoint ordinary-native exclusions cannot execute HA.
    ordinary = await native_peer.rpc("command", text="what time is it", route={"kind": "command"})
    assert ordinary["outcome"] == "error" and len(kitchen.calls) == routine_calls


async def test_actual_node6_telemetry_announce_confirmation_permission_and_current_volume(hass, native_peer):
    await install_devices(hass)
    entry = await native_peer.linked_entry(hass)
    await native_peer.rpc("telemetry", values=VALUES)
    await wait_for(lambda: entry.runtime_data.client.telemetry_available("battery_percent"))
    await hass.async_block_till_done()
    assert hass.states.get(metric_id(hass, native_peer, "battery_percent")).state == "73"
    assert hass.states.get(metric_id(hass, native_peer, "head_touch")).state == "off"
    old = entry.runtime_data.client
    hass.config_entries.async_update_entry(entry, options={"allow_announcements": True})
    await wait_for(
        lambda: (
            entry.runtime_data.client is not old
            and entry.runtime_data.client.ready
            and entry.runtime_data.client.robots[native_peer.robot_id].announcements_allowed
        )
    )
    client = entry.runtime_data.client
    result = await client.async_announce(native_peer.robot_id, "Invented <message> & characters.")
    assert result["outcome"] == "success", result
    state = await native_peer.rpc("status")
    assert len(state["speaks"]) == 1 and "&lt;message&gt;" in state["speaks"][0] and "&amp;" in state["speaks"][0]
    assert "telemetry" not in state["routing"].get("capabilities", [])
    await hass.config_entries.async_remove(entry.entry_id)
    state = await native_peer.rpc("status")
    assert not state["paired"] and state["direct_enabled"]
