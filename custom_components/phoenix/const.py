"""Phoenix connector constants."""

DOMAIN = "phoenix"
VERSION = "0.3.0b2"
MIN_ANNOUNCEMENT_FIRMWARE = "13.2.1"
PROTOCOL_VERSION = 2
PAIRING_VERSION = 1
LOCAL_PORT = 9443
CONTROL_CAPABILITIES = (
    "robot_roster",
    "robot_action",
    "room_context",
    "state_queries",
    "follow_up",
    "routine_shortcuts",
)
CAPABILITIES = (*CONTROL_CAPABILITIES, "telemetry")
DEFAULT_URL = "https://jibo.io"
CONF_PHOENIX_URL = "phoenix_url"
CONF_CODE = "connection_code"
CONF_CONVERSATION_AGENT = "conversation_agent"
CONF_CREDENTIAL = "credential"
CONF_INSTALLATION_ID = "installation_id"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_ROBOT_ID = "robot_id"
CONF_FINGERPRINT = "fingerprint"
CONF_GENERATION = "generation"
CONF_ALLOW_ANNOUNCEMENTS = "allow_announcements"
CONF_LEGACY_DEVICE = "legacy_device_id"
CONF_QUIET_HOURS_ENABLED = "quiet_hours_enabled"
CONF_QUIET_HOURS_START = "quiet_hours_start"
CONF_QUIET_HOURS_END = "quiet_hours_end"
CONF_ROUTINE_SHORTCUTS = "routine_shortcuts"
DEFAULT_QUIET_HOURS_START = "22:00:00"
DEFAULT_QUIET_HOURS_END = "08:00:00"
FOLLOW_UP_SECONDS = 30
ANNOUNCEMENT_SECONDS = 30
MAX_ANNOUNCEMENT_CHARS = 300
MAX_SHORTCUTS = 16
MAX_ROBOTS = 32
MAX_FRAME_BYTES = 8192
MAX_SEEN_REQUESTS = 256
MAX_COMMANDS = 4
