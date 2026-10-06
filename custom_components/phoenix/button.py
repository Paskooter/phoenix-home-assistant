"""Cleanup for native Home Assistant activity without arbitrary skill execution."""

from homeassistant.components.button import ButtonEntity

from .control_entity import PhoenixControlEntity, add_control_entities
from .controls import CONTROL_FEATURES


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(entry, async_add_entities, {feature: PhoenixStop for feature in CONTROL_FEATURES})


class PhoenixStop(PhoenixControlEntity, ButtonEntity):
    _attr_name = "Stop Home Assistant activity"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "stop_activity", "installed_skills")

    @property
    def available(self) -> bool:
        return self.client._control_admission_error(self.robot_id, self.feature, "stop") is None

    async def async_press(self) -> None:
        await self.client.async_control(self.robot_id, "stop")
