"""Actual opt-in agent dispatch, including the Jev fork and its real SDK."""

import json
import os
import time
from uuid import uuid4

import pytest
from homeassistant.components import conversation
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from custom_components.phoenix.client import PhoenixClient
from tests.test_conversation import install_devices
from tests.test_transport import backend as backend_fixture
from tests.test_transport import linked_entry, make_entry, turn, wait_for

backend = backend_fixture


class SyntheticGrok(conversation.ConversationEntity):
    """A real HA agent with synthetic speech; no cloud account involved."""

    _attr_name = "Synthetic Grok"
    _attr_unique_id = "synthetic-grok"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self):
        self.calls = []

    @property
    def supported_languages(self):
        return ["en"]

    async def async_process(self, user_input):
        self.calls.append(user_input.text)
        response = intent.IntentResponse(language=user_input.language)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech("Synthetic reply with <angle> & characters.")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)


async def choose_agent(hass, entry, agent_id, *, key="conversation_agent"):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {key: agent_id})
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()


async def test_options_selects_agent_without_relink_and_default_remains_builtin(hass, backend, monkeypatch):
    kitchen, _ = await install_devices(hass)
    grok = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([grok])
    entry = await linked_entry(hass, backend, monkeypatch)
    data, session = backend
    credential = entry.data["credential"]
    await turn(session, data, "turn on kitchen light")
    assert len(kitchen.calls) == 1
    assert grok.calls == []
    previous = entry.runtime_data.client
    await choose_agent(hass, entry, grok.entity_id)
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.state == "connected")
    assert previous.stopping
    assert previous.session.closed
    assert entry.data["credential"] == credential
    frames, _ = await turn(session, data, "ask Home Assistant to say hello")
    speech = frames[-1]["data"]["action"]["config"]["jcp"]["children"][0]["config"]["play"]["esml"]
    assert "&lt;angle&gt;" in speech and "&amp;" in speech
    assert grok.calls == ["say hello"]
    assert len(kitchen.calls) == 1
    await choose_agent(hass, entry, conversation.HOME_ASSISTANT_AGENT)
    await wait_for(lambda: entry.runtime_data.client.state == "connected")
    await turn(session, data, "turn off kitchen light")
    assert [call[0] for call in kitchen.calls] == ["on", "off"]
    assert grok.calls == ["say hello"]


async def test_removed_selected_agent_fails_before_execution_and_expired_work_does_not_run(hass, monkeypatch):
    kitchen, _ = await install_devices(hass)
    entry = make_entry(
        {"installation_id": str(uuid4()), "credential": "synthetic", "phoenix_url": "https://example.invalid"}
    )
    entry = type(entry)(
        version=1,
        minor_version=1,
        domain="phoenix",
        title="Phoenix",
        data=entry.data,
        source="user",
        unique_id="synthetic",
        options={"conversation_agent": "conversation.missing"},
        discovery_keys={},
        subentries_data=None,
    )
    client = PhoenixClient(hass, entry, None, auth_failed=lambda: None)
    client._welcome({"v": 1, "type": "welcome", "session_id": str(uuid4()), "server_time_ms": int(time.time() * 1000)})
    sent = []

    async def send(frame):
        sent.append(frame)

    monkeypatch.setattr(client, "_send", send)
    await client._execute(str(uuid4()), "turn off kitchen lights", int(time.time() * 1000) + 2000)
    assert sent[-1]["result"]["code"] == "agent_unavailable"
    assert sent[-1]["result"]["outcome"] == "error"
    assert "settings" in sent[-1]["result"]["speech"]
    await client._execute(str(uuid4()), "turn on kitchen lights", 1)
    assert sent[-1]["result"]["outcome"] == "expired"
    assert kitchen.calls == []


async def test_options_rejects_non_agent_entities(hass, backend, monkeypatch):
    await install_devices(hass)
    entry = await linked_entry(hass, backend, monkeypatch)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            flow["flow_id"], {"conversation_agent": "light.kitchen_light"}
        )
    hass.config_entries.options.async_abort(flow["flow_id"])


async def test_real_jev_openrouter_sdk_to_phoenix_speech_and_fallback(hass, backend, monkeypatch):
    project = os.environ.get("JEV_PROJECT_DIR")
    if not project:
        pytest.skip("Set JEV_PROJECT_DIR for the Jev/OpenRouter cross-repository check")
    import httpx2

    kitchen, bedroom = await install_devices(hass)
    grok = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([grok])
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "https://openrouter.ai/api/v1/systemone"
        labels = {
            "category": "command",
            "domain": "light",
            "action": "turn_off",
            "scope": "named_area",
            "target_area": "Kitchen",
        }
        if body.get("state", {}).get("utterance") == "say hello":
            labels["category"] = "conversation"
        answers = {
            key: {"type": "choice", "choice": labels[key], "confidence": 0.95, "probabilities": {labels[key]: 0.95}}
            if question["type"] == "choice"
            else {"type": "noul", "noul": 0.05}
            for key, question in body["questions"].items()
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
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler), trust_env=False),
    )
    configured = await hass.config_entries.flow.async_init(
        "jev_assist", context={"source": "user"}, data={"provider": "openrouter", "api_key": "synthetic-openrouter-key"}
    )
    assert configured["type"] == "create_entry", configured
    jev_entry = configured["result"]
    await hass.async_block_till_done()
    agent_id = er.async_get(hass).async_get_entity_id(
        "conversation", "jev_assist", jev_entry.entry_id + "-conversation"
    )
    assert agent_id
    await choose_agent(hass, jev_entry, grok.entity_id, key="grok_handoff_agent_id")
    entry = await linked_entry(hass, backend, monkeypatch)
    previous = entry.runtime_data.client
    await choose_agent(hass, entry, agent_id)
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.state == "connected")
    frames, latency = await turn(backend[1], backend[0], "turn off the kitchen lights")
    speech = frames[-1]["data"]["action"]["config"]["jcp"]["children"][0]["config"]["play"]["esml"]
    assert "OK" in speech
    assert kitchen.calls == [("off", {})]
    assert bedroom.calls == []
    assert grok.calls == []
    frames, _ = await turn(backend[1], backend[0], "ask Home Assistant to say hello")
    speech = frames[-1]["data"]["action"]["config"]["jcp"]["children"][0]["config"]["play"]["esml"]
    assert "Synthetic reply" in speech and "&lt;angle&gt;" in speech
    assert grok.calls == ["say hello"]
    # Reload transport cannot replay either the action or the conversational handoff.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.state == "connected")
    assert kitchen.calls == [("off", {})]
    assert len(requests) == 3  # one synthetic key check, two classifier turns
    print(f"Synthetic Jev/OpenRouter transport-to-Phoenix result: {latency:.1f} ms (mocked classifier network)")
