"""Native notify.send_message announces over the paired direct TLS link."""

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError

from .const import CONF_ALLOW_ANNOUNCEMENTS, DOMAIN, MIN_ANNOUNCEMENT_FIRMWARE
from .entity import PhoenixRobotEntity


async def async_setup_entry(hass, entry, async_add_entities):
    client = entry.runtime_data.client
    added: set[str] = set()

    @callback
    def add_robots() -> None:
        new = set(client.robots).difference(added)
        added.update(new)
        async_add_entities([PhoenixAnnouncement(client, robot_id) for robot_id in sorted(new)])

    entry.async_on_unload(client.subscribe_roster(add_robots))
    add_robots()


class PhoenixAnnouncement(PhoenixRobotEntity, NotifyEntity):
    """The timestamp advances only after confirmed spoken completion."""

    _attr_entity_category = None

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "announcement")

    @property
    def available(self) -> bool:
        return bool(
            super().available
            and "robot_action" in self.client.capabilities
            and self.robot.online
            and self.client.entry.options.get(CONF_ALLOW_ANNOUNCEMENTS) is True
            and self.robot.announcements_allowed
            and self.robot.announcements_supported
        )

    @property
    def extra_state_attributes(self):
        robot = self.robot
        if not robot or self.client.state != "connected":
            reason = "disconnected"
        elif self.client.entry.options.get(CONF_ALLOW_ANNOUNCEMENTS) is not True or not robot.announcements_allowed:
            reason = "permission_required"
        elif not robot.announcements_supported:
            reason = "firmware_required"
        elif not robot.online:
            reason = "offline"
        else:
            reason = None
        return {"unavailable_reason": reason, "minimum_firmware": MIN_ANNOUNCEMENT_FIRMWARE}

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        if title:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_title")
        await self.client.async_announce(self.robot_id, message)
