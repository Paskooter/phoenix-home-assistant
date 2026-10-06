"""Shared control entities appear only for authenticated native capabilities."""

from collections.abc import Callable

from homeassistant.core import callback

from .entity import PhoenixRobotEntity


@callback
def add_control_entities(entry, async_add_entities, factories: dict[str, Callable]) -> None:
    """Discover additive controls without creating unsupported firmware entities."""
    client = entry.runtime_data.client
    added: set[tuple[str, Callable]] = set()

    @callback
    def add() -> None:
        entities = []
        if "robot_controls" not in client.capabilities:
            return
        for robot_id, robot in client.robots.items():
            for feature, factory in factories.items():
                if feature not in robot.controls_supported or (robot_id, factory) in added:
                    continue
                added.add((robot_id, factory))
                entities.append(factory(client, robot_id))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(client.subscribe_roster(add))
    add()


class PhoenixControlEntity(PhoenixRobotEntity):
    """Native state remains unknown until an authenticated observation arrives."""

    _attr_entity_category = None

    def __init__(self, client, robot_id, key: str, feature: str) -> None:
        super().__init__(client, robot_id, key)
        self.feature = feature

    @property
    def available(self) -> bool:
        return self.client.control_available(self.robot_id, self.feature)

    def observed(self, key: str):
        return self.client.observed_control(key)
