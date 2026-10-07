# Release validation

## 0.4.0b2 / recovery and optional automations

Local validation covers **168 checks on each of Home Assistant 2026.8.1 and
2026.9.4** with Python 3.14.8: 165 public checks and three separately enabled
private endpoint checks on the official Node 6.5.0 runtime. The public suite's
three private-runtime skips were exercised separately on both versions.
The final 2026.9.4 full run passed; the 2026.8.1 full run and all 14 control
checks after the test-only entity-registration correction passed.

New coverage includes connection recovery without replay, transient versus
sustained outages, independent entry cleanup, malformed or unwritable durable
request history, and actionable protocol failures. Eleven recovery checks pass
on each version. Twenty blueprint checks per version use the real Home
Assistant automation engine and invented paired devices to verify state-change
announcements, hold cancellation, cooldowns, first-use announcements, low battery,
missing readings, reconnects, permissions, quiet hours and scheduled sleep/wake.

Request-history failures preserve the ledger and block new actions. A successful
network reconnect cannot clear a storage failure; reloading verifies both
reading and durable writing. Test fixtures bind to isolated loopback ports.
The stale-state test now waits for actual entity registration and exercises the
scheduled expiry callback, stale packets and future timestamps without a narrow
wall-clock observation window. Production timing rules are unchanged.

This companion release changes no robot firmware or OTA catalog. The earlier
hardware results and their limits below remain the hardware evidence; the new
blueprints have software acceptance, not newly claimed physical acceptance.

## 0.4.0b1 / BE 13.3.0 candidate

The [release CI](https://github.com/Paskooter/phoenix-home-assistant/actions/runs/37431060566)
passed HACS, hassfest and both supported HA versions. Local validation used
actual **Home Assistant 2026.8.1 and 2026.9.4**, with Python **3.14.8**. The
2026.8.1 full run passed **137/137**, with no skips. All 137 cases were covered
on 2026.9.4 by public CI and the three private native-endpoint cases.

These checks include real built-in conversation processing, the actual optional
Jev SDK with provider HTTP intercepted, synthetic TLS peers and the private
native robot endpoint on official **Node 6.5.0**. No paid provider or household
action occurred. The exact flat release ZIP also passed a clean-install pairing,
unload/reload and moving-video camera HTTP check on HA 2026.9.4.

Some local full runs on the shared host hit command deadlines while its filesystem
journal stalled. The post-dispatch variants passed in isolation; all three
private Node 6.5.0 cases passed in **5.03 seconds** with synthetic state in an
isolated tmpfs. Protocol deadlines, durable-admission calls and production
storage rules were unchanged. These host failures are recorded separately from
the passing public CI and hardware results.

Coverage includes single-code pairing, wrong/expired/reused codes, mutual proof
and certificate binding, completion recovery, credential revocation, forged
identity, migration, address verification, options, unload/reload, storage,
deadlines, duplicates, reconnect, uncertainty and all 15 telemetry roles.

The combined native-endpoint test exercised real HA control entities through
pinned TLS and the actual native request broker/adapter: multiline text, PNG
upload/display, ring RGB, volume, WAV playback/pause/resume/stop, sleep/wake,
Clock launch/stop, explicit camera preview and touch cleanup. Its native SDK
resources and camera bytes are invented. It confirms the wire contract and
native API calls; it does not claim physical display, sound or camera quality.

Independent control cases cover permission acknowledgement, quiet hours, busy
and touch admission, malformed/absent/stale state, local-media bounds, symlinks,
remote URLs, redirects, lost responses, no replay, unload timers and name changes.
Native nickname/four-word name updates preserve HA's explicit user name, area,
entity IDs and pairing across reload.

The continuous-camera tests use Home Assistant's authenticated camera HTTP
route and a moving VP8 fixture. They verify multiple decoded JPEG frames without
snapshot polling, two-viewer limits, malformed video rejection and decoder
cleanup on disconnect, permission removal, unload, HA shutdown and deadlines.

BE passed **230/230 native host tests**, including the combined JEV routing and
single-code UI checks. The new runtime modules parse on actual
Node **6.5.0**; unchanged vendor-wrapper tests verify native view, media and sound
contracts. The complete committed BE tree supplied the build. Its official
11.0.1 integrity gate reports **21,590 official files**, **21,629 candidate files**,
**39 additions**, **zero missing files** and **zero unresolved package mains**.
The 173,393,920-byte candidate's SHA-256 is
`cc36cdcc139967a264d0eaf531006be05c3da99c48aa54440e934b09bdf93582`.
The official archive is only the integrity reference, never an extracted build
base. BE source and artifacts remain in their separate private project.

The candidate was copied to the designated test robot over SSH and launched
through its native System Manager. The actual renderer returned to idle on
BE 13.3.0; checked runtime files matched the committed build, and the
existing paired identity, credential, certificate and profile were preserved.
Its initialized native adapter advertised all eight control capabilities and
the four installed skill choices. The public OTA offer was not changed by this
development launch.

On the same combined build, developer checks exercised native TextView and
ImageView rendering with private screen captures, ring animation and release,
the unchanged master volume, local WAV playback/pause/resume/stop, sleep/wake,
Clock launch/stop and explicit camera start/stop. Text and image admission took
638 ms and 598 ms; Clock admission took 251 ms; camera start took 787 ms. These
measure native action acknowledgements, not human-perceived or full voice latency.
The robot returned to idle with owned screen, ring, audio and camera resources
released and master volume preserved.

Continuous capture produced a **1280×720 VP8 video-only WebM** on the robot.
The combined-build sample decoded **118 frames** with FFmpeg. Its native TCP
listener was verified to bind only to loopback; stop closed readers and cleared
the durable ownership marker. This proves real camera video, separately from
the synthetic HA decoding test. The read-only routing check also confirmed the
JEV fallback was loaded, native light routing worked, delayed actions were
refused and an ordinary time question retained its normal route. A later
owner-reported shutdown/home-command conflict was reproduced in the actual
Jibo SDK and corrected: an admitted home turn suppresses late cloud actions,
redirects and duplicate results for its exact transaction, while ordinary
requests to turn Jibo himself off retain their native route.

Ruff, formatting, JavaScript syntax, translation parity and archive/lifecycle
checks are release checks. Developer hardware checks do not establish owner
acceptance of the new HA camera card, physical pairing flow or audible output.

## Previous 0.3.0b3 hardware baseline

BE **13.2.2** completed its normal native update and returned to normal startup on
OS **13.0.7**, Services **13.0.8** and the designated test robot. All **21 installed
source checks** passed. The renderer used Electron **1.4.3 / Node 6.5.0**. The
complete official-archive gate passed with 21,617 candidate files and no missing
runtime entry points.

On a physically paired direct session with HA **2026.9.4**, the owner confirmed
that Jibo woke and answered an ordinary time question and turned the approved
single light on and off by voice. Passive native evidence observed genuine
wake/turn/result sequences, and the paired HA connection delivered all **15 live
sensor roles**. A recorded HA processing interval was **826.9 ms**; this measures
HA processing, not complete microphone-to-spoken-response latency.

The published baseline is [0.3.0b3](https://github.com/Paskooter/phoenix-home-assistant/releases/tag/v0.3.0b3).
Its [public CI](https://github.com/Paskooter/phoenix-home-assistant/actions/runs/37412507116)
passed HACS, hassfest and both supported HA versions. That hardware evidence does
not by itself validate the new single-code screen, display/media controls or
new controls or continuous video.

## Limits and remaining physical checks

Owner confirmation of the new single-code screen/completion, audible output,
physical touch/hatch interruption and the complete 0.4.0b1 HA camera-card path
remain outstanding. A fresh human voice turn with the combined JEV build also
remains separate from the earlier owner-confirmed voice baseline. Developer
hardware checks verified pairing preservation, normal startup and the native
controls described above.

No stereo-camera stream, microphone stream, arbitrary Nimbus execution or
weather action from HA is claimed. Existing ordinary Jibo weather
speech retains its normal path. Public fixtures are invented; private household
captures, device identities, credentials and media are excluded from Git.
