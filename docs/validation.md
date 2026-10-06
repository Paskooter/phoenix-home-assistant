# Release validation

## 0.4.0b1 / BE 13.3.0 candidate

The integration passed **123/123 tests on actual Home Assistant 2026.8.1** and
**123/123 on 2026.9.4**, using Python **3.14.8**, with no skips. Both runs included
real built-in conversation processing, the actual optional Jev SDK with provider
HTTP intercepted, synthetic TLS peers and the private native robot endpoint
running on official **Node 6.5.0**. No paid provider or household action occurred.

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

BE passed **211/211 native host tests**. The new runtime modules parse on actual
Node **6.5.0**; unchanged vendor-wrapper tests verify native view, media and sound
contracts. The complete committed BE tree supplied the build. Its official
11.0.1 integrity gate reports **21,590 official files**, **21,628 candidate files**,
**38 additions**, **zero missing files** and **zero unresolved package mains**.
The 173,383,680-byte candidate's SHA-256 is
`3ba1b7ed710f77e9910bf50d03e5276639db68f39f3da1c825a98648711dddfa`.
The official archive is only the integrity reference, never an extracted build
base. BE source and artifacts remain in their separate private project.

The candidate was copied to the designated test robot over SSH and launched
through its native System Manager. The actual renderer returned to idle on
BE 13.3.0; all 13 checked runtime files matched the committed build, and the
existing paired identity, credential, certificate and profile were preserved.
Its initialized native adapter advertised all eight control capabilities and
the four installed skill choices. The public OTA offer was not changed by this
development launch.

Ruff, formatting, JavaScript syntax, translation parity and archive/lifecycle
checks are release checks. Final candidate publication and new physical controls
acceptance are still pending. The source supports these controls, but this file
does not report an unperformed hardware test or invent physical latency.

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
camera preview.

## Limits and remaining physical checks

New pairing layout/completion, native text/image rendering, actual audio/ring
output, direct announcement interruption, sleep/skill controls, camera preview
quality/hatch stop and connection continuity through server restart require
candidate-specific physical evidence. Existing local pairing preservation and
normal candidate startup have been verified on the designated robot.

No full-rate video, stereo-camera stream, microphone stream, arbitrary Nimbus
execution or weather action from HA is claimed. Existing ordinary Jibo weather
speech retains its normal path. Public fixtures are invented; private household
captures, device identities, credentials and media are excluded from Git.
