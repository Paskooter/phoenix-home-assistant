# Direct local protocol

The robot listens on TCP 9443. HA initiates an authenticated, certificate-pinned TLS WebSocket at `/phoenix/local/v1/connect`. Each entry binds one local robot UUID, credential, certificate fingerprint and pairing generation. Protocol numbers and capability negotiation are separate: WebSocket protocol **2** preserves existing local links; pairing protocol **2** creates new single-code links. There is no cloud fallback.

## Pairing

`GET /phoenix/local/v1/identity` reports an untrusted local identity and pairing version. HA makes a TLS-only probe first and pins all subsequent pairing HTTP requests to that certificate. This initial probe grants no trust or action access.

A physical **Start pairing** opens a 120-second window with eight decimal digits. The code stays on the physical UI and in temporary robot memory. HTTP cannot open a pairing window, approve a different owner or retrieve the code.

`POST /phoenix/local/v1/pair/begin` exchanges SRP-6a 3072/SHA-512 ephemeral public values and salt. HA includes a random nonce. A canonical bound transcript includes the DER certificate fingerprint, robot UUID, pair UUID, generation, firmware/name metadata, salt and both ephemeral public values. SRP mutual proofs and bound MACs authenticate that transcript. The code itself and the derived operational credential never appear in pairing request/result JSON.

`POST /phoenix/local/v1/pair/finish` verifies the client proof, commits the locally derived credential durably, revokes the prior pairing and returns the mutually verifiable server proof. An authenticated `POST /phoenix/local/v1/pair/status` can recover that cached completion proof after a lost finish reply. It is read only: it cannot claim a second credential or extend the physical window. Pressing Done preserves the short recovery cache. Expiry removes the cache without revoking a completed pairing.

Only one live candidate is admitted, with a 30-second candidate deadline inside the physical window, at most five attempts per window, a one-second begin spacing and a bounded per-minute rate. Cancellation clears pending material while retaining an existing connection. Replacement and revocation require deliberate local management or a verified paired request. New links reject older pairing versions instead of falling back to an unauthenticated claim.

## Session and home commands

Every WebSocket JSON frame includes `v: 2`, the fresh `session_id` and paired `generation`. The robot welcome provides its identity, server clock, heartbeat and supported capabilities. HA acknowledges `ready` with the explicitly selected protocol agent `home_assistant` and its supported capabilities. Frames are limited to 8192 bytes. Wrong session, identity or generation is rejected.

Capabilities include `robot_roster`, `robot_action`, `room_context`, `state_queries`, `follow_up`, `routine_shortcuts`, `telemetry` and `robot_controls`. Unknown additions do not grant permission. Telemetry/control negotiation is not exposed to Phoenix as a home-routing authority.

Authenticated `preferences` carry exact bounded routine phrases, fresh follow-up hints, `announcements_enabled` and independently opted-in `controls_enabled` groups. Each session starts with permissions off. A roster reports the native nickname or four-word identity, firmware version, online/busy state, supported controls and acknowledged groups. Client-supplied names or addresses cannot replace pairing identity.

After native wake admission, Jibo sends a home `command` with a UUID request ID, robot ID, recognized text, route and server-clock deadline. HA checks expiry and saves admission before local execution. Names, aliases, areas, Assist exposure and actual conversation results remain HA responsibilities. Narrow routing precedes execution: conversation processing is not used as an utterance classifier. Ordinary robot commands and replies in active skills retain native routing.

HA returns `accepted` and then `result`. Results distinguish `success`, `partial`, `error`, `expired` and `uncertain`. Returned text is escaped for ESML before native speech. State queries remain read only; routines are reported as started. Follow-up hints expire within 30 seconds and are cleared on disconnect, restart, error or uncertainty.

## Native controls

A `robot_action` has a UUID request ID, exact local `robot_id`, action, closed payload object and deadline no more than 30 seconds ahead. Both sides save admission before sending/executing. Action names are fixed:

| Permission group | Actions |
| --- | --- |
| Announcements | `announce` |
| `screen` | `display_text`, `display_image`, `clear_screen` |
| `ring_light` | `set_ring_color`, `ring_off` |
| `audio` | `set_volume`, `play_audio`, `pause_audio`, `resume_audio`, `stop_audio` |
| `sleep` | `sleep`, `wake` |
| `skills` | `run_skill` from the advertised closed installed catalog |
| `camera` | `start_camera`, `stop_camera` |
| Paired cleanup | `stop` and owned-resource cleanup |

No action accepts a URL, file path, shell command, JavaScript or arbitrary SDK method. Cleanup is permitted while owned work is busy; it cannot acquire a new resource or stop an unrelated native skill. Native start, completion, stop proof and freshness determine results and state.

`controls_state` carries the exact robot identity, a server-clock `observed_at_ms` and a bounded native `state.values` snapshot. Known fields describe screen, ring, audio, volume, sleep, installed/active skill and camera. Missing or stale values remain unknown. Native controls expire within 60 seconds, and disconnected sessions clear HA observations rather than inventing an off state.

## Local media and camera

`POST /phoenix/local/v1/media` uses the paired bearer credential over pinned TLS. It requires a fixed Content-Length and supported content type: PNG/JPEG up to 1 MiB, or PCM16 WAV up to 8 MiB and 60 seconds. Image dimensions are limited to 1280 × 720. At most two uploads are retained. Media stays in memory for at most 60 seconds and is consumed once. HTTP 201 returns a random `media_id`, exact `content_type` and `size`; a control payload refers only to that ID. Disconnect, revocation and removed permission discard unused media. HA resolves only its configured local Media directories and rejects symlinks, path escapes, redirects and remote media.

`GET /phoenix/local/v1/camera.jpg` requires the same pairing and camera permission. It reads only an already explicitly started visible preview session. It never starts capture. The native adapter requires idle state and a fresh closed-hatch observation, limits snapshots to one per second and uses a fixed loopback preview path without saving gallery photos. Sessions expire within 60 seconds and stop on touch, hatch opening, native preemption, disconnect or revocation. Returned JPEGs are bounded and marked no-store. HA exposes still-image MJPEG rather than full-rate video or microphone data.

## Reliability and lifecycle

Duplicate IDs are never executed again, including after reconnect or process restart. Expired work is discarded. No disconnected action queue or automatic action retry exists. A lost acknowledgement is uncertain because execution may already have happened. A cleanup failure keeps a durable pending record and busy quarantine until stop proof arrives.

Heartbeat sockets remain local and are not Phoenix voice transactions. Actual recognized voice work retains Phoenix's existing transaction lifecycle and guarded server-deployment behavior. HA connection tasks, media reads, pending requests, entity subscriptions and timers belong to entry setup/unload. Removing an entry attempts paired local revocation; ownership or generation changes invalidate existing access.
