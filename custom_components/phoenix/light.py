"""Jibo's native ring appears as an RGB light."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, LightEntity

from .control_entity import PhoenixControlEntity, add_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(entry, async_add_entities, {"ring_color": PhoenixRingLight})


class PhoenixRingLight(PhoenixControlEntity, LightEntity):
    """Report only the native observed color, including native expiry/off."""

    _attr_name = "Ring light"
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_icon = "mdi:circle-outline"

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "ring_light", "ring_color")

    @property
    def is_on(self):
        return self.observed("ring_active")

    @property
    def brightness(self):
        color = self.observed("ring_rgb")
        return max(color) if color is not None else None

    @property
    def rgb_color(self):
        color = self.observed("ring_rgb")
        if color is None or max(color) == 0:
            return color
        # RGB lights expose hue separately from brightness. Both derive from
        # the observed RGB, without storing a desired color as reported state.
        return tuple(round(part * 255 / max(color)) for part in color)

    async def async_turn_on(self, **kwargs) -> None:
        color = kwargs.get(ATTR_RGB_COLOR) or self.rgb_color or (255, 255, 255)
        if ATTR_BRIGHTNESS in kwargs:
            color = tuple(round(part * kwargs[ATTR_BRIGHTNESS] / 255) for part in color)
        await self.client.async_control(self.robot_id, "set_ring_color", {"rgb": list(color)})

    async def async_turn_off(self, **kwargs) -> None:
        await self.client.async_control(self.robot_id, "ring_off")
