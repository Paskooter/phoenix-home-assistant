# Direct pairing v1 and session protocol v2

This is the public wire contract for the **0.3.0b3 direct candidate**, BE **13.2.2**, and Services **13.0.8**. It requires implementation review and direct release validation; historical connector-v1 results do not establish this protocol's acceptance.

The transport is HA-initiated TLS 1.2 to a robot on TCP **9443**, using ECDHE-RSA AES-GCM suites. The robot creates a separate RSA-2048/SHA-256 self-signed endpoint certificate and local UUID. HA pins the lowercase SHA-256 of the complete peer DER certificate. Discovery, including mDNS on UDP 5353, supplies addresses only; manual hosts are supported.

One HA pairing and one active direct socket belong to each robot. Persistent identity and pairing writes must be private, atomic, and durable, reject symlinks, and fail closed on corruption. A paired identity must never regenerate silently.

## Pairing exchange

Only a native physical owner control may open the **120-second** pairing window or approve a candidate. The window uses monotonic expiry and admits one candidate. A new window or aborted HA flow creates fresh client nonce and claim secret. No HTTP request, cloud RPC, or console action can open or approve this window.

Define:

- `H(x)`: lowercase SHA-256 of UTF-8 text.
- `D`: the literal string `phoenix-local-pair-v1\n`.
- Nonces, commitment hashes, claim secrets, and certificate fingerprints: strictly 64 lowercase hexadecimal characters.
- UUID fields: canonical lowercase UUID v4.

HA generates separate random 32-byte `client_nonce` and `claim_secret`, encoded as hex. Hashing the claim secret means hashing its **hex string**, not the raw bytes.

```text
claim_hash = H(claim_secret)
client_commitment = H(D + "client\n" + client_nonce + "\n" + claim_hash)
```

1. HA observes the actual TLS peer certificate before sending `POST /phoenix/local/v1/pair/begin` with `{v:1, client_commitment, claim_hash}`. This initial certificate is bounded pairing input, not a saved trusted pin.
2. The robot fixes the candidate's `pair_id`, local `robot_id`, own fingerprint, `server_nonce`, and original commitments before replying:
   `{v:1, pair_id, robot_id, fingerprint, server_commitment, expires_in:120}`.
   The returned fingerprint must equal HA's observed peer pin.
3. HA sends `POST /phoenix/local/v1/pair/reveal` with `{v:1, pair_id, client_nonce}` using that captured pin. The robot verifies the original client commitment and returns `{v:1, pair_id, server_nonce}`.
4. HA independently verifies the server commitment and calculates the comparison value:

```text
server_commitment = H(D + "server\n" + fingerprint + "\n" + robot_id + "\n"
                      + pair_id + "\n" + client_commitment + "\n"
                      + claim_hash + "\n" + server_nonce)
sas_digest = H(D + "sas\n" + fingerprint + "\n" + robot_id + "\n"
               + pair_id + "\n" + client_nonce + "\n"
               + server_nonce + "\n" + claim_hash)
```

Interpret the first four digest bytes as an unsigned big-endian integer, take modulo 100,000,000, and display eight zero-padded decimal digits. Both the robot and HA show this SAS. An owner must compare the full number, physically approve the robot's live candidate, and explicitly confirm the match in HA.

Only after HA owner confirmation, send pinned-TLS `POST /phoenix/local/v1/pair/finish` with `{v:1, pair_id, claim_secret}`. The robot requires both the exact live physical approval and `H(claim_secret) == claim_hash`. It persists a new generation and fresh 32-byte operational credential before returning:

```text
{v:1, robot_id, credential, generation, name, firmware_version}
```

Pending approval returns HTTP 409/`pairing_pending`; rejection and expiry return HTTP 410/`pairing_rejected` or `pairing_expired`. Invalid claims do not reveal pairing state or credentials. The same exact approved claim may retrieve the same response during the remaining candidate lifetime; it must not create another credential or replacement pairing. Cancel, disconnect, and expiry do not create an operational credential.

`GET /phoenix/local/v1/identity` exposes only a generic name, local UUID, firmware, and protocol version. It cannot authorize pairing or replace an existing pin. `DELETE /phoenix/local/v1/pairing` with the operational bearer credential revokes durably and closes the socket. Physical Forget is independent of HA availability and retains the robot's identity.

## Session negotiation

HA opens:

```text
wss://<robot-host>:9443/phoenix/local/v1/connect
Authorization: Bearer <operational credential>
```

The saved peer certificate pin is required. Every session uses a fresh robot-generated UUID `session_id`. Every JSON frame, including ready, preferences, status, and heartbeat, contains `v:2`, the current `session_id`, and `generation` matching the durable approved pairing. Frames are at most **8192 bytes**. Wrong robot identity, stale generation/session, malformed frames, and unnegotiated routes are rejected.

The robot's `welcome` carries `server_time_ms`, `robot_id`, `generation`, and capabilities. HA's `ready` reports its HA/integration versions and negotiated capabilities, without provider keys.

| Capability | Local behavior |
| --- | --- |
| `robot_roster` | One paired robot and its HA device |
| `robot_action` | Opt-in announcements with correlated completion |
| `room_context` | Registered robot HA device supplies its assigned area |
| `state_queries` | Read exposed state without executing a conversation agent |
| `follow_up` | Per-robot context with a maximum thirty-second lifetime |
| `routine_shortcuts` | Exact owner-selected phrases for exposed scenes/scripts |
| `telemetry` | Fixed read-only robot measurements on the paired LAN session |

The existing ready, preferences, command, accepted, result, cancel, robot_action, action_result, and roster schemas continue with protocol v2 session/identity validation. A roster uses the existing robot fields but contains only the paired local robot UUID. Status/heartbeat runs every **10 seconds** and carries no wake admission or authorization. A persistent socket does not reserve a native voice transaction.

## Voice commands and results

The robot sends `command` only through a genuine native, single-use wake gate. State queries require the same admission. Each request has a fresh UUID `request_id`, exact paired `robot_id`, `text` of at most **500 characters**, a negotiated route, and `deadline_ms` bound to the robot session clock. The normal budget is **7500 ms**, and any budget over **15000 ms** is rejected.

HA persists a request-ID tombstone before `accepted` and execution. It executes locally once, then returns the correlated result. Outcomes retain the existing honest `success`, `partial`, `error`, `uncertain`, and `expired` shapes, with `response_type`, bounded plain `speech`, counts, and optional error code. On/off state confirmation remains within the original voice deadline.

The robot consumes only its matching pending result, rejects late or unsolicited results, escapes returned speech for native ESML, and reports uncertainty after a lost result. Cancellation stops pending admission/work where possible; it does not claim an already issued device action was rolled back. No command is queued or automatically replayed after reconnect or restart.

Ordinary commands and active-skill replies remain reserved. A query route reads Assist-exposed states without invoking an executing agent or service. Room context comes from the HA device registry, not remote text. Unassigned room-relative commands cannot expand silently to every room.

## Preferences and follow-ups

Authenticated HA preferences carry `announcements_enabled` as a boolean, each exact shortcut UUID and phrase, and one paired robot's follow-up availability and expiry. Shortcut entity IDs, states, room names, conversation IDs, and target sets remain in HA. The shortcut set is at most sixteen entries with phrases of at most eighty characters and must fit the frame bound.

The robot privately revalidates routing, reserved native commands, and fresh local context. Follow-up lifetime is at most **30 seconds** and clears on disconnect/restart and failed or uncertain work. HA rechecks exposure and current device capabilities before using targets.

A Phoenix routing declaration in the supported root `context.data.phoenix_local_home` field may contain routing preference/capabilities, an opaque shortcut ID and phrase, and a follow-up hint. It must contain no local endpoint, certificate pin, credential, returned result, or entity state. Cloud hints cannot open a wake admission, extend local expiry, or authorize a request.

Phoenix ASR still supplies trusted recognized text during an admitted voice turn and sees that utterance. Operator-provided firmware remains trusted. The direct protocol excludes unsolicited cloud action authority; it does not provide offline recognition or full transcript privacy.

## Local announcements

HA sends `robot_action` for fixed action `announce`, with the current session, fresh request UUID, paired robot UUID, plain text of at most **300 characters**, and a deadline no longer than **30000 ms**. There is no arbitrary native command, title, or volume field.

The owner's HA-local **Allow announcements** permission defaults off. The endpoint resets permission to false each session and accepts the saved option through authenticated `preferences.announcements_enabled`; its roster reflects this as `announcements_allowed`. No second portal or robot toggle is required. HA applies quiet hours; the robot checks live admission, presence/busy state, permission, and expiry, persists the admission before speech, and uses its existing owned-speech/stop primitives at the current volume. Native voice/HJ or supported local touch interruption participates in that speech lifecycle.

A matching `action_result` acknowledges spoken completion or returns an honest error, expiry, or uncertainty. Lost completion and interrupted speech are not retried. Connection/reboot never replays an announcement. Local announcements and health must remain independent of Phoenix; candidate checks are pending.

## Robot telemetry

Negotiated `telemetry` frames contain the exact session/generation/robot identity, `observed_at_ms` in the monotonic-anchored robot session clock, and a `values` object limited to fourteen scalar measurements. The fifteenth entity, Online, uses live authenticated session health. Native sources are observed locally; a failed source produces null, and a socket loss clears all readings. Normal snapshots run every ten seconds; touch changes and source expiry can publish sooner. HA expires a snapshot after thirty seconds, accounting for its age when received.

The fields are `battery_percent`, `battery_temperature_c`, `camera`, `charging_state`, `cpu_temperature_c`, `fan_percent`, `hatch_open`, `head_touch`, `main_board_temperature_c`, `microphone_rms_db`, `plugged_in`, `sleeping`, `speaker_volume_percent`, and `system_voltage_v`. Temperatures are Celsius; fan and volume are percentages converted from native fractions. Camera reports preview/hatch status, without images. Microphone RMS is a native dB scalar, without audio samples. Unsupported, non-finite, out-of-range, or stale readings are unavailable.

This capability and its values are excluded from the Phoenix routing declaration. Telemetry cannot open a wake admission, execute a home command, or authorize announcements.

## Legacy migration and removal

A protocol-1 entry upgrades to migration-required state and opens no cloud socket. Options remain, while the owner explicitly selects the old robot device/area association or declines room copying. Local stable robot UUIDs are distinct from cloud bindings; old cloud credentials cannot authenticate local pairing.

After successful physical pairing, HA attempts old installation revocation, removes its old cloud credential, and shows manual console cleanup instructions if revocation was offline. Other legacy robots require separate owner-paired entries. Native `direct_enabled` remains sticky independently of credentials: Forget, revocation, reconnect, and restart do not silently reactivate cloud home commands or announcements. There is no cloud fallback.

Entry removal attempts durable local pairing revocation and removes the HA request ledger. Offline removal instructs the owner to use physical Forget. See [installation](installation.md) and [security](security.md).
