"""Real TLS connector, native HA lifecycle and Phoenix's robot-facing envelopes."""

import asyncio
import json
import os
import ssl
import time
from pathlib import Path
from uuid import uuid4

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.phoenix import diagnostics
from custom_components.phoenix.client import PhoenixClient, conversation_result
from custom_components.phoenix.const import DOMAIN
from tests.test_conversation import install_devices


@pytest.fixture
async def backend(tmp_path):
    server_dir = os.environ.get("PHOENIX_SERVER_DIR")
    if not server_dir:
        pytest.skip("Set PHOENIX_SERVER_DIR for cross-repository TLS tests")
    process = await asyncio.create_subprocess_exec(
        "node",
        str(Path(server_dir) / "scripts/home-assistant/isolated-server.mjs"),
        cwd=server_dir,
        env={**os.environ, "PHOENIX_HA_TEST_ROOT": str(tmp_path)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            while line := await process.stdout.readline():
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if "url" in data and "code" in data:
                    break
            else:
                raise AssertionError((await process.stderr.read()).decode())
        context = ssl.create_default_context(cafile=data["certificate"])
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context)) as session:
            yield data, session
    finally:
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)


async def wait_for(predicate, seconds=6):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def turn(session, backend, text, *, hotphrase=True, skill=None, forged_context=False):
    start = time.perf_counter()
    async with session.ws_connect(
        backend["gateway_url"].replace("http://", "ws://") + "/v1/listen",
        headers={"Authorization": f"Bearer {backend['robot_token']}", "x-jibo-robotid": "forged-robot"},
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
                        "accountID": "forged-household" if forged_context else "synthetic-ha-robot",
                        "robotID": "forged-robot" if forged_context else "synthetic-jibo",
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
                    return frames, (time.perf_counter() - start) * 1000
    raise AssertionError("No final robot reply")


async def linked_entry(hass, backend, monkeypatch):
    data, session = backend
    monkeypatch.setattr("custom_components.phoenix.config_flow.async_get_clientsession", lambda hass: session)
    monkeypatch.setattr("custom_components.phoenix.async_get_clientsession", lambda hass: session)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"phoenix_url": data["url"], "connection_code": data["code"]}
    )
    assert result["type"] == "create_entry", result
    entry = result["result"]
    await wait_for(lambda: hasattr(entry, "runtime_data") and entry.runtime_data.client.state == "connected")
    return entry


async def test_real_tls_config_flow_voice_result_reload_unload_remove(hass, backend, monkeypatch):
    kitchen, bedroom = await install_devices(hass)
    entry = await linked_entry(hass, backend, monkeypatch)
    data, session = backend
    latencies = []
    for text in [
        "turn on the kitchen lights",
        "turn off the kitchen lights",
        "set the bedroom light brightness to fifty percent",
        "set bedroom light to blue",
        "activate the dinner scene",
        "ask Home Assistant to run relax script",
    ]:
        frames, latency = await turn(session, data, text)
        latencies.append(round(latency, 1))
        assert frames[-1]["type"] == "SKILL_ACTION", frames[-1]
        assert frames[-1]["data"]["skill"]["id"] == "phoenix-home-assistant"
        speech = frames[-1]["data"]["action"]["config"]["jcp"]["children"][0]["config"]["play"]["esml"]
        assert speech and "couldn't" not in speech, (text, speech)
    assert kitchen.calls and bedroom.calls
    assert any(kwargs.get("brightness") == 128 for _, kwargs in bedroom.calls)
    assert "credential" not in json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
    # A clean reload owns a fresh socket without replaying any device calls.
    count = len(kitchen.calls) + len(bedroom.calls)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.state == "connected")
    assert len(kitchen.calls) + len(bedroom.calls) == count
    unloaded_client = entry.runtime_data.client
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not unloaded_client.commands
    assert unloaded_client.stopping
    async with session.get(data["url"] + "/api/home-assistant", headers={"Cookie": data["owner_cookie"]}) as response:
        assert not (await response.json())["installations"][0]["connected"]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.state == "connected")
    assert await hass.config_entries.async_remove(entry.entry_id)
    async with session.get(data["url"] + "/api/home-assistant", headers={"Cookie": data["owner_cookie"]}) as response:
        assert (await response.json())["installations"] == []
    print("Synthetic TLS transcript-to-result latency (ms):", latencies)


async def test_reconnect_and_revocation_never_replay(hass, backend, monkeypatch):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, backend, monkeypatch)
    data, session = backend
    await turn(session, data, "turn on kitchen light")
    count = len(kitchen.calls)
    async with session.post(data["url"] + "/test/disconnect"):
        pass
    await wait_for(lambda: entry.runtime_data.client.state != "connected")
    await wait_for(lambda: entry.runtime_data.client.state == "connected")
    assert len(kitchen.calls) == count
    async with session.post(data["url"] + "/test/revoke"):
        pass
    await wait_for(lambda: entry.runtime_data.client.state == "authentication_required")
    assert len(kitchen.calls) == count


async def test_forged_context_and_ordinary_routes_do_not_execute_assist(hass, backend, monkeypatch):
    kitchen, bedroom = await install_devices(hass)
    await linked_entry(hass, backend, monkeypatch)
    data, session = backend
    frames, _ = await turn(session, data, "turn on kitchen lights", forged_context=True)
    assert frames[-1]["type"] == "ERROR"
    for text in [
        "turn up the volume",
        "go to sleep",
        "what time is it",
        "tell me a joke",
        "what is the weather",
        "cancel",
    ]:
        frames, _ = await turn(session, data, text)
        assert frames[-1].get("data", {}).get("skill", {}).get("id") != "phoenix-home-assistant"
    await turn(session, data, "turn on kitchen lights", hotphrase=False, skill="active-skill")
    assert kitchen.calls == bedroom.calls == []


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_relink_flow_replaces_credentials(hass, backend, monkeypatch, source):
    await install_devices(hass)
    entry = await linked_entry(hass, backend, monkeypatch)
    data, session = backend
    previous_credential = entry.data["credential"]
    async with session.post(data["url"] + "/test/revoke"):
        pass
    await wait_for(lambda: entry.runtime_data.client.state == "authentication_required")
    for flow in hass.config_entries.flow.async_progress():
        if flow["handler"] == DOMAIN:
            hass.config_entries.flow.async_abort(flow["flow_id"])
    async with session.post(
        data["url"] + "/api/home-assistant/codes",
        headers={"Cookie": data["owner_cookie"], "X-Phoenix-API-Client": "synthetic-test"},
        json={"robotIds": ["synthetic-jibo"], "name": "Relink fixture"},
    ) as response:
        assert response.status == 200
        code = (await response.json())["code"]
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": source, "entry_id": entry.entry_id},
        data=entry.data if source == "reauth" else None,
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"phoenix_url": data["url"], "connection_code": code}
    )
    assert result["type"] == "abort"
    assert result["reason"] == ("reauth_successful" if source == "reauth" else "reconfigure_successful")
    await wait_for(
        lambda: (
            entry.data["credential"] != previous_credential
            and hasattr(entry, "runtime_data")
            and entry.runtime_data.client.state == "connected"
        )
    )
    async with session.delete(
        data["url"] + "/api/home-assistant/installation", headers={"Authorization": f"Bearer {previous_credential}"}
    ) as response:
        assert response.status == 401


async def test_config_flow_invalid_code_https_and_single_use(hass, backend, monkeypatch):
    await install_devices(hass)
    data, session = backend
    monkeypatch.setattr("custom_components.phoenix.config_flow.async_get_clientsession", lambda hass: session)
    invalid = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"phoenix_url": "http://insecure.example", "connection_code": data["code"]},
    )
    assert invalid["errors"]["base"] == "invalid_url"
    hass.config_entries.flow.async_abort(invalid["flow_id"])
    await linked_entry(hass, backend, monkeypatch)
    reused = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"phoenix_url": data["url"], "connection_code": data["code"]}
    )
    assert reused["errors"]["base"] == "invalid_code"


def make_entry(data):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Phoenix",
        data=data,
        source="user",
        unique_id="synthetic",
        options={},
        discovery_keys={},
        subentries_data=None,
    )


async def test_deduplication_expiry_and_restart_storage(hass, monkeypatch):
    await install_devices(hass)
    entry = make_entry(
        {"installation_id": str(uuid4()), "credential": "synthetic", "phoenix_url": "https://example.invalid"}
    )
    client = PhoenixClient(hass, entry, None, auth_failed=lambda: None)
    client._welcome({"v": 1, "type": "welcome", "session_id": str(uuid4()), "server_time_ms": int(time.time() * 1000)})
    sent = []

    async def send(frame):
        sent.append(frame)

    monkeypatch.setattr(client, "_send", send)
    request_id = str(uuid4())
    frame = {
        "v": 1,
        "type": "command",
        "session_id": client.session_id,
        "request_id": request_id,
        "robot_id": str(uuid4()),
        "text": "turn on kitchen light",
        "language": "en",
        "deadline_ms": int(time.time() * 1000) + 3000,
    }
    await client._receive(frame)
    await client._receive(frame)
    await asyncio.gather(*client.commands.values())
    calls = len(hass.data["light"].get_entity("light.kitchen_light").calls)
    assert calls == 1
    await client._receive(frame)
    assert len(hass.data["light"].get_entity("light.kitchen_light").calls) == calls

    restarted = PhoenixClient(hass, entry, None, auth_failed=lambda: None)
    restarted.seen = await restarted.storage.async_load()
    restarted._welcome(
        {"v": 1, "type": "welcome", "session_id": str(uuid4()), "server_time_ms": int(time.time() * 1000)}
    )
    monkeypatch.setattr(restarted, "_send", send)
    await restarted._receive({**frame, "session_id": restarted.session_id})
    assert sent[-1]["result"]["outcome"] == "uncertain"
    await restarted._receive(
        {**frame, "session_id": restarted.session_id, "request_id": str(uuid4()), "deadline_ms": 1}
    )
    assert sent[-1]["result"]["outcome"] == "expired"
    assert len(hass.data["light"].get_entity("light.kitchen_light").calls) == calls


async def test_slow_state_confirmation_and_lost_confirmation_do_not_repeat(hass, backend, monkeypatch):
    kitchen, _ = await install_devices(hass)
    await linked_entry(hass, backend, monkeypatch)
    data, session = backend
    original = kitchen.async_turn_on

    async def slow_on(**kwargs):
        await asyncio.sleep(0.35)
        await original(**kwargs)

    monkeypatch.setattr(kitchen, "async_turn_on", slow_on)
    frames, latency = await turn(session, data, "turn on kitchen light")
    assert frames[-1]["type"] == "SKILL_ACTION"
    assert latency >= 300
    assert len(kitchen.calls) == 1
    assert hass.states.get(kitchen.entity_id).state == "on"

    # A service can accept a call without confirming a requested state. HA's
    # intent handler would otherwise announce success after its 200ms window.
    async def unconfirmed_off(**kwargs):
        kitchen.calls.append(("off", kwargs))

    monkeypatch.setattr(kitchen, "async_turn_off", unconfirmed_off)
    frames, _ = await turn(session, data, "turn off kitchen light")
    speech = frames[-1]["data"]["action"]["config"]["jcp"]["children"][0]["config"]["play"]["esml"]
    assert "couldn't confirm" in speech
    assert [call[0] for call in kitchen.calls] == ["on", "off"]
    await asyncio.sleep(0.1)
    assert len(kitchen.calls) == 2


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"response_type": "action_done", "data": {"success": [{}], "failed": [{}]}}, "partial"),
        ({"response_type": "error", "data": {"code": "no_valid_targets"}}, "error"),
        ({"response_type": "error", "data": {"code": "failed_to_handle"}}, "uncertain"),
    ],
)
def test_honest_outcomes(data, expected):
    assert conversation_result({"response": data})["outcome"] == expected
