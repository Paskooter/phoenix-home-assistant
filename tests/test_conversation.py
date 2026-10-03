"""Exercise the actual deterministic agent, exposure controls and returned speech."""

from homeassistant.components import conversation
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import Context
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix.client import conversation_result


class SyntheticLight(LightEntity):
    """An isolated light recording real intent-driven service calls."""

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_is_on = False
    _attr_brightness = 255
    _attr_rgb_color = (255, 255, 255)

    def __init__(self, name, unique_id):
        self._attr_name = name
        self._attr_unique_id = unique_id
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", kwargs))
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self._attr_rgb_color = kwargs.get("rgb_color", self._attr_rgb_color)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", kwargs))
        self._attr_is_on = False
        self.async_write_ha_state()


class SyntheticSwitch(SwitchEntity):
    """A real HA switch platform with an invented device."""

    _attr_should_poll = False
    _attr_name = "Garden Switch"
    _attr_unique_id = "synthetic-garden-switch"
    _attr_is_on = False

    def __init__(self):
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append("on")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append("off")
        self._attr_is_on = False
        self.async_write_ha_state()


async def install_devices(hass):
    assert await async_setup_component(hass, "light", {})
    kitchen = SyntheticLight("Kitchen Light", "synthetic-kitchen")
    bedroom = SyntheticLight("Bedroom Light", "synthetic-bedroom")
    await hass.data["light"].async_add_entities([kitchen, bedroom])
    area = ar.async_get(hass).async_create("Kitchen")
    er.async_get(hass).async_update_entity(kitchen.entity_id, area_id=area.id, aliases={"Cooking Lamp"})
    for entity in [kitchen, bedroom]:
        exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, True)
    assert await async_setup_component(
        hass, "scene", {"scene": [{"name": "Dinner", "entities": {kitchen.entity_id: "on"}}]}
    )
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "relax": {
                    "alias": "Relax",
                    "sequence": [{"action": "light.turn_on", "target": {"entity_id": bedroom.entity_id}}],
                }
            }
        },
    )
    exposed_entities.async_expose_entity(hass, "conversation", "scene.dinner", True)
    exposed_entities.async_expose_entity(hass, "conversation", "script.relax", True)
    assert await async_setup_component(hass, "conversation", {})
    return kitchen, bedroom


async def process(hass, text):
    answer = await conversation.async_converse(
        hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=conversation.HOME_ASSISTANT_AGENT,
    )
    return conversation_result(answer.as_dict(), hass)


async def test_actual_agent_lights_aliases_areas_brightness_color_scene_script(hass):
    kitchen, bedroom = await install_devices(hass)
    for text in [
        "turn on the kitchen lights",
        "turn off the kitchen lights",
        "turn on Cooking Lamp",
        "set the bedroom light brightness to fifty percent",
        "set bedroom light to blue",
        "activate the dinner scene",
        "run relax script",
    ]:
        result = await process(hass, text)
        assert result["outcome"] == "success", (text, result)
        assert result["speech"], text
    assert any(call[0] == "off" for call in kitchen.calls)
    assert any(call[1].get("brightness") == 128 for call in bedroom.calls)
    assert any("rgb_color" in call[1] for call in bedroom.calls)
    assert hass.states.get(kitchen.entity_id).state == "on"


async def test_hidden_and_unknown_entities_do_not_execute(hass):
    kitchen, _ = await install_devices(hass)
    exposed_entities.async_expose_entity(hass, "conversation", kitchen.entity_id, False)
    for text in ["turn on kitchen light", "turn on nonexistent light"]:
        result = await process(hass, text)
        assert result["outcome"] == "error", result
    assert kitchen.calls == []


async def test_actual_switch_and_unavailable_light(hass):
    kitchen, _ = await install_devices(hass)
    assert await async_setup_component(hass, "switch", {})
    switch = SyntheticSwitch()
    await hass.data["switch"].async_add_entities([switch])
    exposed_entities.async_expose_entity(hass, "conversation", switch.entity_id, True)
    for text in ["turn on garden switch", "turn off garden switch"]:
        result = await process(hass, text)
        assert result["outcome"] == "success", result
    assert switch.calls == ["on", "off"]
    kitchen._attr_available = False
    kitchen.async_write_ha_state()
    result = await process(hass, "turn on kitchen light")
    assert result["outcome"] in ("error", "uncertain"), result
    assert kitchen.calls == []


async def test_partial_room_request(hass):
    kitchen, _ = await install_devices(hass)
    failed = SyntheticLight("Kitchen Counter Light", "synthetic-unavailable")
    failed._attr_available = False
    await hass.data["light"].async_add_entities([failed])
    area = ar.async_get(hass).async_get_area_by_name("Kitchen")
    er.async_get(hass).async_update_entity(failed.entity_id, area_id=area.id)
    exposed_entities.async_expose_entity(hass, "conversation", failed.entity_id, True)
    result = await process(hass, "turn on kitchen lights")
    assert kitchen.calls and not failed.calls
    assert result["outcome"] == "partial", result
