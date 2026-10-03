"""Connection health."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import PhoenixEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PhoenixConnected(entry.runtime_data.client)])


class PhoenixConnected(PhoenixEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, client):
        super().__init__(client, "connected")

    @property
    def is_on(self):
        return self.client.state == "connected"
