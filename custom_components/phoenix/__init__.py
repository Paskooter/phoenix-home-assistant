"""Direct local Jibo voice control with an owner-selected Assist agent."""

import logging
from dataclasses import dataclass

from homeassistant.components import persistent_notification
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import PhoenixError, revoke
from .client import PhoenixClient
from .const import CONF_ALLOW_ANNOUNCEMENTS, DOMAIN
from .ledger import RequestLedger
from .local_api import normalize_endpoint, reject_redirects, revoke_pairing

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NOTIFY,
    Platform.TEXT,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.CAMERA,
]


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
    client.initialize_robot()

    def exposure_changed(_entity_ids=None) -> None:
        if client.ready:
            entry.async_create_background_task(hass, client.async_refresh_preferences(), "Phoenix exposure preferences")

    entry.async_on_unload(exposed_entities.async_listen_entity_updates(hass, "conversation", exposure_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, client.async_run(), "Phoenix connector")
    if entry.data.get("transport") == "pairing_required":
        entry.async_start_reauth(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry[PhoenixData]) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[PhoenixData]) -> bool:
    """Stop network and command work when unloading/restarting."""
    await entry.runtime_data.client.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Revoke the independent local key, never reconnect a legacy cloud socket."""
    if entry.data.get("transport") == "pairing_required":
        if not await revoke(
            async_get_clientsession(hass), entry.data.get("phoenix_url", ""), entry.data.get("credential", "")
        ):
            persistent_notification.async_create(
                hass,
                "Remove this old Home Assistant installation in your Phoenix console. The server was unreachable "
                "during removal. No cloud connector was restarted.",
                title="Finish cloud connection cleanup",
                notification_id=f"{DOMAIN}_cloud_cleanup",
            )
        if installation_id := entry.data.get("installation_id"):
            await Store(hass, 1, f"phoenix.{installation_id}.requests").async_remove()
    local_revoked = True
    if entry.data.get("transport") == "local":
        try:
            local_revoked = await revoke_pairing(
                async_get_clientsession(hass),
                normalize_endpoint(entry.data["host"], entry.data["port"]),
                entry.data["fingerprint"],
                entry.data["credential"],
            )
        except PhoenixError, KeyError:
            local_revoked = False
    if not local_revoked:
        _LOGGER.warning("Local pairing could not be revoked while removing the entry; use Forget on Jibo")
        persistent_notification.async_create(
            hass,
            "Use Settings → Home Assistant → Forget on Jibo to finish removing this local connection. "
            "The robot was unreachable during removal. Cloud home control will stay disabled.",
            title="Forget Jibo's local pairing",
            notification_id=f"{DOMAIN}_removal",
        )
    await RequestLedger(hass, entry.entry_id).async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Make old cloud entries inert until their owner completes local pairing."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "transport": "pairing_required"},
            options={**entry.options, CONF_ALLOW_ANNOUNCEMENTS: False},
            version=2,
        )
    return True
