"""Useful connection states without publishing household information."""

from homeassistant.components import conversation
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import callback

from .entity import PhoenixEntity, PhoenixRobotEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PhoenixConnectionStatus(entry.runtime_data.client)])
    client = entry.runtime_data.client
    added: set[str] = set()

    @callback
    def add_robots() -> None:
        new = set(client.robots).difference(added)
        added.update(new)
        async_add_entities(
            [
                entity(client, robot_id)
                for robot_id in sorted(new)
                for entity in (PhoenixRobotResponse, PhoenixRobotLatency, PhoenixRobotAgent)
            ]
        )

    entry.async_on_unload(client.subscribe_roster(add_robots))
    add_robots()


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


class PhoenixRobotResponse(PhoenixRobotEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["success", "partial", "error", "uncertain", "expired"]

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "last_response")

    @property
    def native_value(self):
        return self.robot.last_outcome if self.robot else None

    @property
    def extra_state_attributes(self):
        if not self.robot:
            return {}
        return {"response_type": self.robot.last_response, "last_error": self.robot.last_error}


class PhoenixRobotLatency(PhoenixRobotEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "response_latency")

    @property
    def native_value(self):
        return self.robot.latency_ms if self.robot else None


class PhoenixRobotAgent(PhoenixRobotEntity, SensorEntity):
    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "selected_agent")

    @property
    def native_value(self):
        if info := conversation.async_get_agent_info(self.hass, self.client.agent_id):
            return info.name
        return "Unavailable"
