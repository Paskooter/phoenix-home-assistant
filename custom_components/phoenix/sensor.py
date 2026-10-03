"""Useful connection states without publishing household information."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import PhoenixEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PhoenixConnectionStatus(entry.runtime_data.client)])


class PhoenixConnectionStatus(PhoenixEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "connecting", "disconnected", "authentication_required", "protocol_error"]

    def __init__(self, client):
        super().__init__(client, "connection_status")

    @property
    def native_value(self):
        return self.client.state

    @property
    def extra_state_attributes(self):
        return {"last_error": self.client.last_error}
