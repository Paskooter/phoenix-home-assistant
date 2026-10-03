"""Shared diagnostic entities; no device-control services are exposed."""

from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo, Entity

from .client import PhoenixClient
from .const import DOMAIN, VERSION


class PhoenixEntity(Entity):
    """An entity reading cached connection health."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client: PhoenixClient, key: str) -> None:
        self.client = client
        self._attr_unique_id = f"{client.entry.data['installation_id']}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.entry.unique_id)},
            name="Phoenix",
            manufacturer="Phoenix",
            model="Assist voice connector",
            sw_version=VERSION,
            configuration_url=f"{client.entry.data['phoenix_url']}/app#/home-assistant",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.client.subscribe(self.async_write_ha_state))
