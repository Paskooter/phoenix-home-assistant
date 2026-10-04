"""Owner linking, reauthentication and moving to a self-hosted Phoenix."""

from datetime import time
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import conversation
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PhoenixError, exchange_code, normalize_url, revoke
from .commands import exposed, validate_shortcuts
from .const import (
    CONF_CODE,
    CONF_CONVERSATION_AGENT,
    CONF_PHOENIX_URL,
    CONF_QUIET_HOURS_ENABLED,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_ROUTINE_SHORTCUTS,
    DEFAULT_QUIET_HOURS_END,
    DEFAULT_QUIET_HOURS_START,
    DEFAULT_URL,
    DOMAIN,
)


class PhoenixConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Exchange a one-time owner code; never request HA credentials."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PhoenixOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._link("user", user_input)

    async def async_step_reauth(self, _entry_data: dict[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        return await self._link("reauth_confirm", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        return await self._link("reconfigure", user_input)

    async def _link(self, step: str, user_input: dict[str, Any] | None):
        errors = {}
        old_entry = (
            self._get_reauth_entry()
            if step == "reauth_confirm"
            else (self._get_reconfigure_entry() if step == "reconfigure" else None)
        )
        default_url = old_entry.data[CONF_PHOENIX_URL] if old_entry else DEFAULT_URL
        if user_input:
            try:
                url = normalize_url(user_input[CONF_PHOENIX_URL])
                linked = await exchange_code(async_get_clientsession(self.hass), url, user_input[CONF_CODE])
                data = {CONF_PHOENIX_URL: url, **linked}
                unique_id = f"{url}/{linked['installation_id']}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                if old_entry:
                    if old_entry.data["credential"] != linked["credential"]:
                        await revoke(
                            async_get_clientsession(self.hass),
                            old_entry.data[CONF_PHOENIX_URL],
                            old_entry.data["credential"],
                        )
                    return self.async_update_reload_and_abort(old_entry, unique_id=unique_id, data=data)
                return self.async_create_entry(title="Phoenix", data=data)
            except PhoenixError as err:
                errors["base"] = err.code
        schema = vol.Schema(
            {
                vol.Required(CONF_PHOENIX_URL, default=default_url): selector.TextSelector(),
                vol.Required(CONF_CODE): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)


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
