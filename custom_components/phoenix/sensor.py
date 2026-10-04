"""Useful connection states without publishing household information."""

from datetime import timedelta

from homeassistant.components import conversation
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import MAX_COMMANDS, MIN_ANNOUNCEMENT_FIRMWARE
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
                for entity in (PhoenixRobotResponse, PhoenixRobotLatency, PhoenixRobotAgent, PhoenixAnnouncementStatus)
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


class PhoenixAnnouncementStatus(PhoenixRobotEntity, SensorEntity):
    """Cached local readiness remains readable while native notify is unavailable."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "disconnected",
        "unsupported_server",
        "permission_required",
        "firmware_required",
        "quiet_hours",
        "offline",
        "busy",
        "ready",
    ]
    _attr_extra_state_attributes = {"minimum_firmware": MIN_ANNOUNCEMENT_FIRMWARE}

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "announcement_status")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._refresh_status, timedelta(seconds=30), name="Phoenix announcement status"
            )
        )

    @callback
    def _refresh_status(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        client = self.client
        robot = self.robot
        if (
            client.stopping
            or not client.ready
            or client.state != "connected"
            or client.socket is None
            or client.socket.closed
            or not robot
        ):
            return "disconnected"
        if "robot_action" not in client.capabilities:
            return "unsupported_server"
        if not robot.announcements_allowed:
            return "permission_required"
        if not robot.announcements_supported:
            return "firmware_required"
        if client._quiet_hours():
            return "quiet_hours"
        if not robot.online:
            return "offline"
        if (
            robot.busy
            or self.robot_id in client._robot_commands
            or any(action.robot_id == self.robot_id for action in client.pending_actions.values())
            or len(client.commands) + len(client.pending_actions) >= MAX_COMMANDS
        ):
            return "busy"
        return "ready"


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
