"""Owner linking, reauthentication and moving to a self-hosted Phoenix."""

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PhoenixError, exchange_code, normalize_url, revoke
from .const import CONF_CODE, CONF_PHOENIX_URL, DEFAULT_URL, DOMAIN


class PhoenixConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Exchange a one-time owner code; never request HA credentials."""

    VERSION = 1

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
