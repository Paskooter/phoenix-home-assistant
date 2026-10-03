"""Diagnostics intentionally exclude credentials, IDs, targets and transcripts."""

from .const import PROTOCOL_VERSION, VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    client = entry.runtime_data.client
    return {
        "integration_version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "connection_state": client.state,
        "last_error": client.last_error,
        "active_commands": len(client.commands),
    }
