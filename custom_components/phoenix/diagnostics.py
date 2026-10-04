"""Diagnostics intentionally exclude credentials, IDs, targets and transcripts."""

from homeassistant.components import conversation

from .const import PROTOCOL_VERSION, VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    client = entry.runtime_data.client
    return {
        "integration_version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "connection_state": client.state,
        "last_error": client.last_error,
        "active_commands": len(client.commands),
        "active_announcements": len(client.pending_actions),
        "capabilities": sorted(client.capabilities),
        "robot_count": len(client.robots),
        "agent_kind": "built_in" if client.agent_id == conversation.HOME_ASSISTANT_AGENT else "selected",
        "agent_available": conversation.async_get_agent(hass, client.agent_id) is not None,
        "robots": [
            {
                "online": robot.online,
                "busy": robot.busy,
                "announcements_allowed": robot.announcements_allowed,
                "announcements_supported": robot.announcements_supported,
                "last_response": robot.last_response,
                "last_outcome": robot.last_outcome,
                "latency_ms": robot.latency_ms,
                "commands_completed": robot.commands_completed,
            }
            for robot in client.robots.values()
        ],
    }
