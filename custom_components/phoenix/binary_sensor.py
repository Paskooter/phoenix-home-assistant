"""Connection health."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback

from .entity import PhoenixEntity, PhoenixRobotEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PhoenixConnected(entry.runtime_data.client)])
    client = entry.runtime_data.client
    added: set[str] = set()

    @callback
    def add_robots() -> None:
        new = set(client.robots).difference(added)
        added.update(new)
        async_add_entities([PhoenixRobotOnline(client, robot_id) for robot_id in sorted(new)])

    entry.async_on_unload(client.subscribe_roster(add_robots))
    add_robots()


class PhoenixConnected(PhoenixEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, client):
        super().__init__(client, "connected")

    @property
    def is_on(self):
        return self.client.state == "connected"


class PhoenixRobotOnline(PhoenixRobotEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "robot_online")

    @property
    def is_on(self):
        return self.robot.online if self.robot else False

    @property
    def extra_state_attributes(self):
        return {
            "busy": self.robot.busy if self.robot else None,
            "announcements_allowed": self.robot.announcements_allowed if self.robot else False,
            "announcements_supported": self.robot.announcements_supported if self.robot else False,
        }
