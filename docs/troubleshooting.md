# Troubleshooting

## Connection recovery

Phoenix reports connection and request-storage problems under **Settings → System → Repairs**. Each issue names the affected Jibo and explains the next step. Brief network interruptions reconnect silently. A local connection that remains unavailable for five minutes raises an issue; retries do not restart that grace period. The issue clears after a verified reconnect. Disabling or removing the integration clears its issues and pending reminders.

If Jibo's address changed, open **Settings → Devices & services → Phoenix**, use the affected entry's menu and select **Reconfigure**. Enter his current local address. This checks the saved certificate, credential and robot identity before changing the entry, so an address change does not require pairing again. A changed certificate or rejected credential uses Home Assistant's separate physical reauthentication flow.

A protocol mismatch, unexpected paired identity or replaced connection stops reconnecting and shows a specific repair. Check software compatibility or duplicate Home Assistant instances before reloading. Reloading does not retry past commands, announcements or controls.

If request history cannot be read or saved, Phoenix blocks new work. Check Home Assistant's disk space, permissions and logs, then reload the entry. Startup checks both reading and durably writing the existing request ledger before connecting. Preserve that ledger and the existing pairing; deleting history is not a repair for a storage failure. A working network connection alone does not clear a storage failure.

## Pairing

New pairing requires BE **13.3.0**. Start on Jibo first and enter his address and all eight digits in HA within two minutes. Leading zeroes count. Both devices complete automatically; there is no second Approve step. An existing verified local pairing does not need replacement for an upgrade.

A wrong or expired code creates no access. Start a fresh window if it expired. If a completion reply was lost, check whether Jibo shows Connected before using Manage → Replace connection to start again. Do not copy pairing credentials between installations.

## Missing controls or permission errors

In **Phoenix → Configure**, enable only the permissions you want. Screen, ring, audio, sleep, skills and camera each start off. Controls require a fresh authenticated roster, acknowledged permission and supported native firmware. A busy robot or active head touch blocks new work. Stop/cleanup stay available for the integration's owned activity.

Camera capture needs a closed hatch, an idle robot and **Camera: Turn on**. Opening a card alone does not start capture. The session expires within 60 seconds; missing or stale observations become unavailable. Use another explicit session to resume. Home Assistant decodes Jibo's native continuous video into the card's live MJPEG feed; viewing the card never extends the session.

Images and audio must be in HA's configured local Media directory. Use a `media-source://media_source/` identifier, not an HTTP URL or filesystem path. Check PNG/JPEG dimensions and size, or PCM16 WAV encoding, duration and size. MP3 and OGG are unsupported. See [robot controls](robot-controls.md).

## Commands, room context, and routines

Expose the target to Assist and check its name, alias, area, features, and availability. Try the intended sentence in the selected HA agent. Use “ask Home Assistant to…” for custom sentences.

Assign the paired Jibo device to an HA area for “here” and generic room lights. Migration requires an explicit old device/area selection; it must not infer a room from a cloud UUID or spoken text.

If a follow-up has no context, name the target again. Context lasts at most thirty seconds for that robot and clears after disconnect/restart or an inconclusive result. Every follow-up still needs a new wake turn. A routine must match its configured phrase and select a currently exposed scene or script.

A missing selected conversation agent fails without fallback. Its provider or inference latency may exceed the short voice deadline. Read-only state questions use the local query path, not that agent.

## Announcements

Check the entry's local **Allow announcements** option, quiet hours, local connection, compatible firmware, and robot readiness. Permission defaults off. The legacy console permission does not grant direct local permission.

Use the robot's Announcement entity with a plain message of at most 300 characters and no title or volume override. A busy/offline robot or quiet-hours rejection never creates a queued message.

A lost completion acknowledgement means uncertainty. Check whether speech happened before retrying manually. Reconnect, restart, or native interruption must not replay it. Native touch/voice interruption behavior and independence from Phoenix restarts still need direct candidate validation.

## Removal and private diagnostics

If removal could not reach Jibo, open **Settings → Home Assistant → Manage → Disconnect** on Jibo and confirm **Disconnect**. HA unload/disable alone stops its socket without proving durable revocation. Direct mode stays selected after revocation or Disconnect; the old cloud home-action path does not reactivate.

Diagnostics should contain bounded status, protocol/version, error codes, anonymous counts, and timing; they should omit credentials, certificate pins, addresses, names, household identifiers, and utterances. Do not share config-entry storage, request ledgers, robot identity files, private captures, or backups. Report the generic error and exact software versions instead.

See [installation and migration](installation.md), [security](security.md), and [pending validation](validation.md).
