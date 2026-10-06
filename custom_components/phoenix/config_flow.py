"""Local physical pairing, re-pairing and migration from the cloud bridge."""

from datetime import time
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import conversation, persistent_notification
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import PhoenixError, revoke
from .commands import device_area, exposed, validate_shortcuts
from .const import (
    CONF_ALLOW_ANNOUNCEMENTS,
    CONF_CODE,
    CONF_CONVERSATION_AGENT,
    CONF_HOST,
    CONF_INSTALLATION_ID,
    CONF_LEGACY_DEVICE,
    CONF_PHOENIX_URL,
    CONF_PORT,
    CONF_QUIET_HOURS_ENABLED,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_ROUTINE_SHORTCUTS,
    DEFAULT_QUIET_HOURS_END,
    DEFAULT_QUIET_HOURS_START,
    DOMAIN,
    LOCAL_PORT,
)
from .local_api import begin_pairing, finish_pairing, normalize_endpoint, verify_pairing


class PhoenixConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Pair automatically using the code displayed after physical Start on Jibo."""

    VERSION = 2

    def __init__(self) -> None:
        self._old_entry: config_entries.ConfigEntry | None = None
        self._host = ""
        self._port = LOCAL_PORT
        self._legacy_device: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PhoenixOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._pair("user", user_input)

    async def async_step_reauth(self, _entry_data: dict[str, Any]):
        self._old_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        self._old_entry = self._get_reauth_entry()
        return await self._pair("reauth_confirm", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        self._old_entry = self._get_reconfigure_entry()
        if self._old_entry.data.get("transport") == "local":
            entry = self._old_entry
            errors = {}
            if user_input:
                try:
                    host, port = user_input[CONF_HOST].strip().lower(), user_input.get(CONF_PORT, LOCAL_PORT)
                    await verify_pairing(
                        async_get_clientsession(self.hass),
                        normalize_endpoint(host, port),
                        entry.data["fingerprint"],
                        entry.data["credential"],
                        entry.data["robot_id"],
                        entry.data["generation"],
                    )
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host, CONF_PORT: port})
                except PhoenixError as err:
                    errors["base"] = err.code
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): selector.TextSelector(),
                        vol.Optional(CONF_PORT, default=entry.data[CONF_PORT]): vol.All(
                            vol.Coerce(int), vol.Range(min=1, max=65535)
                        ),
                    }
                ),
                errors=errors,
            )
        return await self._pair("reconfigure", user_input)

    def _legacy_devices(self):
        if not self._old_entry or self._old_entry.data.get("transport") != "pairing_required":
            return {}
        prefix = str(self._old_entry.data.get(CONF_INSTALLATION_ID, "")) + "/"
        return {
            device.id: device
            for device in dr.async_entries_for_config_entry(dr.async_get(self.hass), self._old_entry.entry_id)
            if any(domain == DOMAIN and identifier.startswith(prefix) for domain, identifier in device.identifiers)
        }

    async def _pair(self, step: str, user_input: dict[str, Any] | None):
        errors = {}
        old_entry = self._old_entry
        legacy_devices = self._legacy_devices()
        if user_input:
            try:
                self._host, self._port = user_input[CONF_HOST].strip().lower(), user_input.get(CONF_PORT, LOCAL_PORT)
                endpoint = normalize_endpoint(self._host, self._port)
                selected = user_input.get(CONF_LEGACY_DEVICE, "none")
                if legacy_devices and selected not in ("none", *legacy_devices):
                    raise PhoenixError("invalid_legacy_device")
                self._legacy_device = selected if selected in legacy_devices else None
                candidate = await begin_pairing(
                    async_get_clientsession(self.hass), endpoint, user_input.get(CONF_CODE, "")
                )
                if (
                    old_entry
                    and old_entry.data.get("transport") == "local"
                    and old_entry.unique_id != candidate.robot_id
                ):
                    return self.async_abort(reason="wrong_robot")
                await self.async_set_unique_id(candidate.robot_id)
                if any(
                    entry.unique_id == candidate.robot_id and (not old_entry or entry.entry_id != old_entry.entry_id)
                    for entry in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                linked = await finish_pairing(async_get_clientsession(self.hass), candidate)
                return await self._complete_pairing(linked)
            except PhoenixError as err:
                errors["base"] = err.code
        schema = {
            vol.Required(
                CONF_HOST, default=old_entry.data.get(CONF_HOST, "") if old_entry else ""
            ): selector.TextSelector(),
            vol.Optional(
                CONF_PORT, default=old_entry.data.get(CONF_PORT, LOCAL_PORT) if old_entry else LOCAL_PORT
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }
        schema[vol.Required(CONF_CODE)] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
        if legacy_devices:
            schema[vol.Required(CONF_LEGACY_DEVICE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "none", "label": "Do not copy a room"},
                        *[
                            {"value": device.id, "label": device.name_by_user or device.name or "Jibo"}
                            for device in legacy_devices.values()
                        ],
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)

    async def _complete_pairing(self, linked: dict[str, Any]):
        """Persist only the verified operational credential and robot metadata."""
        data = {"transport": "local", CONF_HOST: self._host, CONF_PORT: self._port, **linked}
        if self._legacy_device:
            data["migration_area_id"] = device_area(self.hass, self._legacy_device)
        old_entry = self._old_entry
        if old_entry:
            legacy_installation = old_entry.data.get(CONF_INSTALLATION_ID)
            if old_entry.data.get("transport") == "pairing_required" and old_entry.data.get("credential"):
                removed = await revoke(
                    async_get_clientsession(self.hass),
                    old_entry.data[CONF_PHOENIX_URL],
                    old_entry.data["credential"],
                )
                if not removed:
                    persistent_notification.async_create(
                        self.hass,
                        "Local pairing is ready. Remove the old Home Assistant installation in your Phoenix "
                        "console; the old server was unreachable during cleanup. The local connection "
                        "does not use that cloud credential.",
                        title="Finish cloud connection cleanup",
                        notification_id=f"{DOMAIN}_cloud_cleanup",
                    )
            result = self.async_update_reload_and_abort(
                old_entry, unique_id=linked["robot_id"], title=linked["name"], data=data
            )
            if legacy_installation:
                await Store(self.hass, 1, f"phoenix.{legacy_installation}.requests").async_remove()
            return result
        return self.async_create_entry(title=linked["name"], data=data)


class PhoenixOptionsFlow(config_entries.OptionsFlow):
    """Local speech settings and exact owner-chosen exposed routines."""

    async def async_step_init(self, user_input=None):
        choices = [{"value": conversation.HOME_ASSISTANT_AGENT, "label": "Home Assistant (built-in)"}]
        for state in self.hass.states.async_all("conversation"):
            info = conversation.async_get_agent_info(self.hass, state.entity_id)
            if info and info.id != conversation.HOME_ASSISTANT_AGENT:
                choices.append({"value": info.id, "label": info.name})
        allowed = {choice["value"] for choice in choices}
        errors = {}
        current_options = self.config_entry.options
        try:
            shortcuts = validate_shortcuts(current_options.get(CONF_ROUTINE_SHORTCUTS, []))
        except ValueError:
            shortcuts = []
        if user_input is not None:
            if user_input[CONF_CONVERSATION_AGENT] not in allowed:
                errors["base"] = "invalid_agent"
            else:
                try:
                    options = {
                        CONF_CONVERSATION_AGENT: user_input[CONF_CONVERSATION_AGENT],
                        CONF_ALLOW_ANNOUNCEMENTS: user_input.get(
                            CONF_ALLOW_ANNOUNCEMENTS, current_options.get(CONF_ALLOW_ANNOUNCEMENTS, False)
                        ),
                        CONF_QUIET_HOURS_ENABLED: user_input.get(
                            CONF_QUIET_HOURS_ENABLED, current_options.get(CONF_QUIET_HOURS_ENABLED, False)
                        ),
                        CONF_QUIET_HOURS_START: user_input.get(
                            CONF_QUIET_HOURS_START,
                            current_options.get(CONF_QUIET_HOURS_START, DEFAULT_QUIET_HOURS_START),
                        ),
                        CONF_QUIET_HOURS_END: user_input.get(
                            CONF_QUIET_HOURS_END, current_options.get(CONF_QUIET_HOURS_END, DEFAULT_QUIET_HOURS_END)
                        ),
                    }
                    try:
                        time.fromisoformat(options[CONF_QUIET_HOURS_START])
                        time.fromisoformat(options[CONF_QUIET_HOURS_END])
                    except (TypeError, ValueError) as err:
                        raise ValueError("invalid_quiet_hours") from err
                    shortcuts = [item for item in shortcuts if item["id"] not in user_input.get("remove_shortcuts", [])]
                    phrase, target = user_input.get("shortcut_phrase", "").strip(), user_input.get("shortcut_target")
                    if phrase or target:
                        if not phrase or not target:
                            raise ValueError("invalid_shortcut")
                        shortcuts = validate_shortcuts(
                            [*shortcuts, {"id": str(uuid4()), "phrase": phrase, "entity_id": target}]
                        )
                        if self.hass.states.get(target) is None or not exposed(self.hass, target):
                            raise ValueError("shortcut_not_exposed")
                    options[CONF_ROUTINE_SHORTCUTS] = shortcuts
                    return self.async_create_entry(title="", data=options)
                except ValueError as err:
                    errors["base"] = str(err)
        current = self.config_entry.options.get(CONF_CONVERSATION_AGENT, conversation.HOME_ASSISTANT_AGENT)
        if current not in allowed:
            current = conversation.HOME_ASSISTANT_AGENT
        schema = {
            vol.Required(CONF_CONVERSATION_AGENT, default=current): selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(
                CONF_ALLOW_ANNOUNCEMENTS, default=current_options.get(CONF_ALLOW_ANNOUNCEMENTS, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_QUIET_HOURS_ENABLED, default=current_options.get(CONF_QUIET_HOURS_ENABLED, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_QUIET_HOURS_START, default=current_options.get(CONF_QUIET_HOURS_START, DEFAULT_QUIET_HOURS_START)
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_QUIET_HOURS_END, default=current_options.get(CONF_QUIET_HOURS_END, DEFAULT_QUIET_HOURS_END)
            ): selector.TimeSelector(),
            vol.Optional("shortcut_phrase"): selector.TextSelector(),
            vol.Optional("shortcut_target"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["scene", "script"])
            ),
        }
        if shortcuts:
            schema[vol.Optional("remove_shortcuts", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": item["id"], "label": item["phrase"]} for item in shortcuts],
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema), errors=errors)
