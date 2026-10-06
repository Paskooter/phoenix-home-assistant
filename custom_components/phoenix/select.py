"""Launch a reviewed native skill from the robot's authenticated installed catalog."""

from homeassistant.components.select import SelectEntity

from .control_entity import PhoenixControlEntity, add_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(entry, async_add_entities, {"installed_skills": PhoenixInstalledSkill})


class PhoenixInstalledSkill(PhoenixControlEntity, SelectEntity):
    """Options are installed native skills; selecting one starts it once."""

    _attr_name = "Start installed skill"
    _attr_icon = "mdi:robot"

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "installed_skill", "installed_skills")

    @property
    def catalog(self):
        return self.observed("installed_skills") or []

    @property
    def available(self) -> bool:
        return super().available and self.observed("installed_skills") is not None and bool(self.catalog)

    def _labels(self):
        names = [skill["name"] for skill in self.catalog]
        return {
            skill["id"]: skill["name"] if names.count(skill["name"]) == 1 else f"{skill['name']} ({skill['id']})"
            for skill in self.catalog
        }

    @property
    def options(self):
        return list(self._labels().values())

    @property
    def current_option(self):
        return self._labels().get(self.observed("active_skill_id"))

    async def async_select_option(self, option: str) -> None:
        skill_id = next((skill_id for skill_id, label in self._labels().items() if label == option), None)
        if skill_id is None:
            self.client._control_error("invalid_payload")
        await self.client.async_control(self.robot_id, "run_skill", {"skill_id": skill_id})
