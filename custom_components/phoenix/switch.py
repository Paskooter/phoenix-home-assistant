"""Sleep uses the native sleep/wake handlers and genuine circadian state."""

from homeassistant.components.switch import SwitchEntity

from .control_entity import PhoenixControlEntity, add_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(entry, async_add_entities, {"sleep_control": PhoenixSleep})


class PhoenixSleep(PhoenixControlEntity, SwitchEntity):
    _attr_name = "Sleep"
    _attr_icon = "mdi:sleep"

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "sleep_control", "sleep_control")

    @property
    def is_on(self):
        return self.observed("sleeping")

    async def async_turn_on(self, **kwargs) -> None:
        await self.client.async_control(self.robot_id, "sleep")

    async def async_turn_off(self, **kwargs) -> None:
        await self.client.async_control(self.robot_id, "wake")
