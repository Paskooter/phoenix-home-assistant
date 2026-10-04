# Connector protocol 1

The integration exchanges a one-time owner code through `POST /api/home-assistant/exchange` with JSON `{"code":"…"}`. The server returns `v: 1`, a UUID `installation_id`, and an opaque `credential`. Use `Authorization: Bearer <credential>` on an outbound `wss://<Phoenix origin>/api/home-assistant/connect` connection. HTTP redirects are refused and TLS certificates are verified. Code exchange is not retried automatically.

All WebSocket frames are JSON objects, at most 8192 bytes, with `v: 1`. The server sends a `welcome` with `session_id` (UUID), `server_time_ms`, `heartbeat_ms: 20000`, and optionally `capabilities`. The connector responds with `ready`, the same session ID, `agent: "home_assistant"`, `ha_version`, `integration_version`, and its supported capabilities. The `agent` field identifies the HA connector for protocol-1 compatibility. By default it explicitly passes `conversation.HOME_ASSISTANT_AGENT` to `async_converse` (currently `conversation.home_assistant`). Since 0.1.0b4, an owner may select another installed agent in local HA options; that explicit ID is passed instead. A missing selected agent fails before invocation, without fallback. Existing links remain valid without relinking.

The server sends `command` with the session ID, UUID `request_id`, opaque UUID `robot_id`, English `text` of at most 500 characters, `language: "en"`, and absolute server-clock `deadline_ms`. The robot ID is an installation binding, not a robot account or household identifier. The welcome's clock anchor and elapsed monotonic time determine remaining time on the HA side. Server command budgets are 7.5 seconds; a connector rejects a budget over 15 seconds.

Before execution the connector persists a bounded request-ID tombstone, then sends `accepted`. It invokes HA locally once. `result` includes request/session IDs and a result object: `outcome` (`success`, `partial`, `error`, `uncertain`, or `expired`), `response_type` (`action_done`, `query_answer`, or `error`), plain `speech` (at most 500 characters), and optional error `code`, success/failed counts, and conversation ID. Phoenix validates correlation and deadlines, ignores late/duplicate results, escapes text using its existing ESML response builder, and never retries an uncertain action.

Live duplicate IDs return the cached result or acceptance; remembered IDs after reconnect/restart return uncertainty without execution. Expired commands return `expired`. There is at most one pending action per robot and four per connection. No work is replayed after reconnect or restart. Tombstones survive at least until a request deadline plus sixty seconds; after that the original request is already expired.

WebSocket ping/pong detects stale connectors independently of voice work. Close `4001` requires reauthentication, `4002` stops a replaced connection, and `4003` stops an incompatible/malformed protocol. Transport loss reconnects with backoff. `DELETE /api/home-assistant/installation` with the credential revokes that installation.

A socket or heartbeat is not a Hub voice transaction. Actual Gateway listen/execute/reply work remains in the existing transaction lifecycle, including deployment admission and cancellation. Authorization is derived from verified Gateway robot claims and live Account ownership records. Household IDs in client context or headers confer no access.

Phoenix remains part of the command trust boundary. Its server receives the robot's recognized command text and can originate command frames to the linked installation. TLS and installation credentials authenticate the HA–Phoenix connection; they do not independently prove a physical wake word or the owner's spoken intent, and this is not end-to-end robot–HA authentication or encryption. Operators can see forwarded command text and returned speech. Built-in Assist exposure and the integration's local query/routine checks limit their respective paths; an owner-selected third-party agent uses that agent's own locally configured capabilities and permissions.

## Negotiated features in 0.2.0b1

Features are enabled only when both sides advertise the corresponding capability. A missing `welcome.capabilities` means legacy voice control: HA sends no roster-dependent preferences or reverse requests.

| Capability | Behavior |
| --- | --- |
| `robot_roster` | Authenticated installation-scoped robot roster and HA devices |
| `robot_action` | Live reverse announcements and confirmed results |
| `room_context` | Registered robot `device_id` supplied to conversation processing |
| `state_queries` | Local read-only state queries |
| `follow_up` | Thirty seconds of per-robot conversation and resolved-target context |
| `routine_shortcuts` | Exact owner-configured phrases for Assist-exposed scenes/scripts |

Negotiated commands may include `route: {"kind":"command"|"query"|"follow_up"|"routine"}`. A routine also carries its UUID `shortcut_id`. A legacy command without a route is a normal command. Route metadata carries no household claims, HA entity IDs, area names, conversation IDs, or arbitrary agent prompts.

A query route never invokes a conversation agent or device service. It reads only current Assist-exposed states and resolves explicit names/aliases or areas. Supported questions cover on/off, open/closed, lock state, temperature, and humidity. Missing, ambiguous, unexposed, or unavailable targets fail without an executing fallback. Question-shaped follow-ups use the same read-only path.

Normal commands receive the registered robot's HA device ID, so an owner-assigned HA area supplies room context. Remote text/context cannot assign a room. Bare device-group commands such as "turn on the lights" require that area; an unassigned Jibo cannot silently expand them to all exposed rooms. HA waits for resolved light/switch on/off state changes within the original voice deadline. A scene or script is reported as started, without claiming all its downstream device states.

## Robot roster and local preferences

The server sends roster snapshots after negotiation:

```json
{"v":1,"session_id":"<uuid>","type":"roster","robots":[{"robot_id":"<binding-uuid>","name":"Example Jibo","online":true,"busy":false,"announcements_allowed":false,"announcements_supported":false}]}
```

HA registers one device per opaque binding, preserving its local area across reloads. Names stay in the owner's device registry. Optional `announcements_supported` defaults to false and describes native receiver support independently of ordinary home voice features. A missing receiver must not disable room context, commands, queries, routines, or follow-ups. Announcements require BE 13.1.0 or later and explicit owner permission.

HA sends bounded preferences only after negotiation:

```json
{"v":1,"session_id":"<uuid>","type":"preferences","shortcuts":[{"id":"<shortcut-uuid>","phrase":"reading time"}],"follow_up":[{"robot_id":"<binding-uuid>","available":true,"expires_at_ms":1791072030000}]}
```

The global shortcut list has at most sixteen entries; each phrase has at most eighty characters, and the combined serialized phrase list is bounded to leave room for all follow-up hints in the 8192-byte frame. Matching normalizes case, whitespace, a curly apostrophe, and trailing sentence punctuation, then requires an exact match. Ordinary Jibo commands and active-skill answers remain reserved. Local HA entity IDs never leave HA. Owners must configure each phrase and its specific scene/script. HA checks live Assist exposure while offering it and immediately before executing it. An unknown ID or a different phrase cannot select a routine.

Follow-up hints contain no conversation IDs, target IDs, or utterances. Account rechecks the live binding and limits them to thirty seconds. HA retains conversation IDs and successful resolved targets briefly in memory per robot and selected agent. It clears them on expiry, disconnect, unload/restart, agent change, a failed new command, or a new routine. Target reuse rechecks Assist exposure and live light feature capabilities before service dispatch. An unsupported color, temperature, or brightness request leaves the target unchanged. A lost confirmation clears context. No context IDs or utterances are persisted or exported.

The exact follow-ups "make it/them/those dimmer" and "make it/them/those brighter" use only that robot's fresh resolved light targets. Each step changes the observed brightness by 26 on HA's 0–255 scale (approximately ten percentage points), clamped to that range. Every target must already be on, exposed, available, and brightness-supported, with a finite brightness reading within 0–255. HA checks the entire group before dispatch, then issues one absolute brightness call per target and confirms its requested brightness and on/off state within the original command deadline. It never turns on an off light to discover its level. Missing or invalid context and unsupported targets produce no calls; exposure/state changes or lost confirmation after dispatch produce partial success or uncertainty, clear context, and never retry or delegate the phrase to another agent.

## Reverse announcements

Use the native HA action `notify.send_message` with the Jibo announcement entity and plain `message`. The entity is available when the connection, native receiver, robot presence, and explicit permission are available. Its `unavailable_reason` distinguishes disconnected, required permission, required firmware, and offline states. Titles are unsupported. Announcements use Jibo's current master volume. Local options set optional quiet hours in HA's time zone. Quiet hours can cross midnight; equal start/end means all day. Older stored volume options are ignored and removed when settings are saved.

The integration sends one request over the existing outbound authenticated TLS connection:

```json
{"v":1,"session_id":"<uuid>","type":"robot_action","request_id":"<uuid>","robot_id":"<binding-uuid>","action":"announce","text":"An invented announcement.","deadline_ms":1791072030000}
```

The fixed action is `announce`; text is nonempty plain text of at most 300 characters, and the default deadline is thirty seconds. The server permits at most forty-five seconds. Reverse requests contain no household/account claims, HA credentials, or arbitrary robot commands. The integration sends no volume field and does not change Jibo's master volume. The server rejects any supplied `volume` property with `unsupported_volume` before admitting or dispatching the request.

Phoenix rechecks the live installation, binding, ownership, permission, receiver, presence, busy state, and deadline. It durably reserves the UUID before dispatch and never replays pending or uncertain work. Expired announcements cannot enter a durable notification queue. Quiet hours, permission, offline, and busy errors reject without queuing.

The server returns `action_result` with the same request and robot UUIDs and `result: {"outcome":"success"|"error"|"uncertain"|"expired","code":"optional_error_code","speech":""}`. Success requires confirmed spoken completion from the robot. HA validates both UUIDs and waits only within the live deadline. Its notify timestamp advances only on success. Timeout, disconnect, or lost completion means uncertainty; HA never automatically retries the announcement.

## Lifecycle and diagnostics

All background work and subscriptions belong to the HA config entry. Unloading closes the socket, cancels commands, clears context, and resolves pending announcement uncertainty. Reverse dispatch participates in Phoenix's transaction/deployment lifecycle; roster heartbeats do not.

Local per-robot sensors show the last result type/outcome, latency, selected agent, and connection status. Downloaded diagnostics include anonymous counts, boolean status, response types/outcomes, and timing only. They exclude credentials, server origins, names, robot/device/request/conversation IDs, target IDs, area names, shortcut phrases, and utterances.

The integration uses HA's [native notify entity](https://developers.home-assistant.io/docs/core/entity/notify/) and [conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/). Exposure follows HA's [Assist entity exposure controls](https://www.home-assistant.io/voice_control/voice_remote_expose_devices/). Compatibility is checked against actual HA 2026.8.1 and 2026.9.4.
