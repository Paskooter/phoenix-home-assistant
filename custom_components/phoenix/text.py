"""A native screen text entity and bounded image/text display services."""

import voluptuous as vol
from homeassistant.components.text import TextEntity
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform

from .control_entity import PhoenixControlEntity, add_control_entities
from .controls import MAX_SCREEN_TEXT
from .controls_api import async_upload_media


async def async_setup_entry(hass, entry, async_add_entities):
    platform = entity_platform.async_get_current_platform()
    duration = {vol.Optional("duration_ms"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60_000))}
    platform.async_register_entity_service(
        "show_text", {vol.Required("text"): cv.string, **duration}, "async_show_text"
    )
    platform.async_register_entity_service(
        "show_image", {vol.Required("media_content_id"): cv.string, **duration}, "async_show_image"
    )
    platform.async_register_entity_service("clear_screen", {}, "async_clear_screen")
    add_control_entities(entry, async_add_entities, {"screen_text": PhoenixScreenText})


class PhoenixScreenText(PhoenixControlEntity, TextEntity):
    """Text and image state comes from native completion/lifecycle events."""

    _attr_name = "Screen text"
    _attr_native_min = 0
    _attr_native_max = MAX_SCREEN_TEXT
    _attr_icon = "mdi:monitor"

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "screen_text", "screen_text")

    @property
    def native_value(self):
        return self.observed("screen_text")

    @property
    def extra_state_attributes(self):
        return {
            "screen_active": self.observed("screen_active"),
            "image_media_id": self.observed("screen_image_id"),
        }

    async def async_set_value(self, value: str) -> None:
        if value == "":
            await self.async_clear_screen()
        else:
            await self.async_show_text(value)

    async def async_show_text(self, text: str, duration_ms: int | None = None) -> None:
        payload = {"text": text}
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        await self.client.async_control(self.robot_id, "display_text", payload)

    async def async_show_image(self, media_content_id: str, duration_ms: int | None = None) -> None:
        media_id = await async_upload_media(self.client, self.robot_id, media_content_id, "image", self.entity_id)
        payload = {"media_id": media_id}
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        await self.client.async_control(self.robot_id, "display_image", payload)

    async def async_clear_screen(self) -> None:
        await self.client.async_control(self.robot_id, "clear_screen")
