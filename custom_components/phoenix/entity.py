"""Shared cached entities and registered robot devices."""

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


class PhoenixRobotEntity(Entity):
    """Local entities share the opaque, installation-scoped robot device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client: PhoenixClient, robot_id: str, key: str) -> None:
        self.client = client
        self.robot_id = robot_id
        self._attr_unique_id = f"{client.entry.data['installation_id']}_{robot_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{client.entry.data['installation_id']}/{robot_id}")},
            manufacturer="Jibo",
            model="Jibo",
        )

    @property
    def robot(self):
        return self.client.robots.get(self.robot_id)

    @property
    def available(self) -> bool:
        return (
            self.client.state == "connected" and "robot_roster" in self.client.capabilities and self.robot is not None
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.client.subscribe(self.async_write_ha_state))
