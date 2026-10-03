"""Phoenix voice control through an owner-selected Home Assistant Assist agent."""

import logging
from dataclasses import dataclass

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import revoke
from .client import PhoenixClient, reject_redirects
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


@dataclass
class PhoenixData:
    """Config entry owns its long-lived connector."""

    client: PhoenixClient


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[PhoenixData]) -> bool:
    """Create entities and a lifecycle-bound reconnect task."""
    # ws_connect follows redirects by default. Own a session that rejects them,
    # sharing HA's TLS connector without changing other integrations' sessions.
    session = async_create_clientsession(hass, auto_cleanup=False, middlewares=(reject_redirects,))
    entry.async_on_unload(session.detach)
    client = PhoenixClient(hass, entry, session, auth_failed=lambda: entry.async_start_reauth(hass))
    entry.runtime_data = PhoenixData(client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, client.async_run(), "Phoenix connector")
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry[PhoenixData]) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[PhoenixData]) -> bool:
    """Stop network and command work when unloading/restarting."""
    await entry.runtime_data.client.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Revoke on removal; explain offline revocation without blocking removal."""
    if not await revoke(async_get_clientsession(hass), entry.data["phoenix_url"], entry.data["credential"]):
        _LOGGER.warning("Phoenix could not revoke the removed installation; disconnect it in the Phoenix console")
        persistent_notification.async_create(
            hass,
            "Finish removing this connection in Phoenix: open the console, choose Home Assistant, "
            "and disconnect the installation. The server was unreachable during removal.",
            title="Disconnect Phoenix",
            notification_id=f"{DOMAIN}_removal",
        )
    await Store(hass, 1, f"phoenix.{entry.data['installation_id']}.requests").async_remove()
