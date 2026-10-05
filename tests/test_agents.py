"""Explicit agent selection over local TLS, including the actual Jev SDK."""

import json
import os
from uuid import uuid4

import pytest
from homeassistant.components import conversation
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from tests.direct_backend import linked_entry, wait_for
from tests.test_conversation import install_devices


class SyntheticGrok(conversation.ConversationEntity):
    _attr_name = "Synthetic Grok"
    _attr_unique_id = "synthetic-grok"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self):
        self.calls = []

    @property
    def supported_languages(self):
        return ["en"]

    async def async_process(self, user_input):
        self.calls.append(user_input)
        response = intent.IntentResponse(language=user_input.language)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech("Synthetic reply with <angle> & characters.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id or str(uuid4())
        )


async def choose_agent(hass, entry, agent_id, *, key="conversation_agent", **options):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {key: agent_id, **options})
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()


async def test_default_builtin_and_explicit_selected_agent_never_repair_or_fallback(hass, robot):
    kitchen, _ = await install_devices(hass)
    agent = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([agent])
    entry = await linked_entry(hass, robot)
    credentials = dict(entry.data)
    result, _ = await robot.command("turn on kitchen lights")
    assert result["outcome"] == "success" and len(kitchen.calls) == 1 and agent.calls == []
    previous = entry.runtime_data.client
    await choose_agent(hass, entry, agent.entity_id)
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.ready)
    result, _ = await robot.command("say hello")
    assert result["speech"] == "Synthetic reply with <angle> & characters."
    assert [call.text for call in agent.calls] == ["say hello"] and len(kitchen.calls) == 1
    assert dict(entry.data) == credentials and previous.stopping
    cid = entry.runtime_data.client.contexts[robot.robot_id].conversation_id
    await robot.command("and in study", route={"kind": "follow_up"})
    assert agent.calls[-1].conversation_id == cid
    await choose_agent(hass, entry, conversation.HOME_ASSISTANT_AGENT)
    await wait_for(lambda: entry.runtime_data.client.ready)
    await robot.command("turn off kitchen lights")
    assert [call[0] for call in kitchen.calls] == ["on", "off"]
    assert len(agent.calls) == 2


async def test_missing_selected_agent_does_not_execute_builtin(hass, robot):
    kitchen, _ = await install_devices(hass)
    entry = await linked_entry(hass, robot)
    previous = entry.runtime_data.client
    hass.config_entries.async_update_entry(entry, options={"conversation_agent": "conversation.missing"})
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.ready)
    result, _ = await robot.command("turn on kitchen light")
    assert result["outcome"] == "error" and result["code"] == "agent_unavailable"
    assert kitchen.calls == [] and entry.runtime_data.client.contexts == {}


async def test_options_reject_non_agent_entities(hass, robot):
    await install_devices(hass)
    entry = await linked_entry(hass, robot)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            flow["flow_id"], {"conversation_agent": "light.kitchen_light"}
        )
    hass.config_entries.options.async_abort(flow["flow_id"])


async def test_actual_jev_sdk_controls_and_opt_in_conversation_handoff(hass, robot, monkeypatch):
    if not os.environ.get("JEV_PROJECT_DIR"):
        pytest.skip("Set JEV_PROJECT_DIR to the public Jev repository for its SDK integration check")
    import httpx2

    kitchen, bedroom = await install_devices(hass)
    agent = SyntheticGrok()
    await hass.data["conversation"].async_add_entities([agent])
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
            key: {
                "type": "choice",
                "choice": labels.get(key, "none"),
                "confidence": 0.95,
                "probabilities": {labels.get(key, "none"): 0.95},
            }
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
    await choose_agent(hass, jev_entry, agent.entity_id, key="grok_handoff_agent_id")
    entry = await linked_entry(hass, robot)
    previous = entry.runtime_data.client
    await choose_agent(hass, entry, agent_id)
    await wait_for(lambda: entry.runtime_data.client is not previous and entry.runtime_data.client.ready)
    result, _ = await robot.command("turn off kitchen lights")
    assert result["outcome"] == "success" and kitchen.calls == [("off", {})] and not bedroom.calls and not agent.calls
    result, _ = await robot.command("are the kitchen lights on", route={"kind": "query"})
    assert result["outcome"] == "success" and kitchen.calls == [("off", {})]
    result, _ = await robot.command("say hello")
    assert "Synthetic reply" in result["speech"] and [call.text for call in agent.calls] == ["say hello"]
    assert requests  # Real SDK exercised, every provider request intercepted above.
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert kitchen.calls == [("off", {})] and len(agent.calls) == 1
