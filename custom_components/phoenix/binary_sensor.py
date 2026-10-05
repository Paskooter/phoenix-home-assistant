"""Connection health."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback

from .entity import PhoenixEntity, PhoenixRobotEntity
from .telemetry import BINARY_DESCRIPTIONS


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PhoenixConnected(entry.runtime_data.client)])
    client = entry.runtime_data.client
    added: set[str] = set()

    @callback
    def add_robots() -> None:
        new = set(client.robots).difference(added)
        added.update(new)
        async_add_entities([PhoenixRobotOnline(client, robot_id) for robot_id in sorted(new)])
        async_add_entities(
            [
                PhoenixTelemetryBinarySensor(client, robot_id, description)
                for robot_id in sorted(new)
                for description in BINARY_DESCRIPTIONS
            ]
        )

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
    def available(self) -> bool:
        return True

    @property
    def is_on(self):
        return bool(
            self.client.ready
            and self.client.state == "connected"
            and self.client.socket is not None
            and not self.client.socket.closed
            and self.robot is not None
        )

    @property
    def extra_state_attributes(self):
        return {
            "busy": self.robot.busy if self.robot else None,
            "announcements_allowed": self.robot.announcements_allowed if self.robot else False,
            "announcements_supported": self.robot.announcements_supported if self.robot else False,
        }


class PhoenixTelemetryBinarySensor(PhoenixRobotEntity, BinarySensorEntity):
    """Use genuine observed booleans, never absence as an off/closed reading."""

    def __init__(self, client, robot_id, description):
        super().__init__(client, robot_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.client.telemetry_available(self.entity_description.key)

    @property
    def is_on(self):
        return self.client.telemetry_values.get(self.entity_description.key)
