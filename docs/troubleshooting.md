# Direct connection troubleshooting

This page covers the **0.3.0b2 direct candidate**. Hardware and release acceptance are pending. The old cloud code/socket instructions apply only to legacy 0.2 releases.

| Symptom | What to check |
| --- | --- |
| Cannot find the robot's address | Check its local network address and compatible Services mode. The candidate HA form accepts a manual host; an mDNS/UDP 5353 result is only a hint and still requires physical pairing and pin validation. |
| Cannot connect locally | HA must reach the robot on TCP 9443. Check LAN routing, isolation/firewall rules, host, compatible BE/Services, and whether the endpoint is running. Do not expose the port to the Internet. |
| Pairing unavailable or pending | Open the robot's physical 120-second pairing window. Only one candidate fits that window. Compare and approve on Jibo, then confirm in HA. A cloud code cannot open the window. |
| Pairing screen closes unexpectedly | BE 13.2.0 exhibited a pairing-screen interruption that canceled the attempt. The corrected BE 13.2.1 target still needs hardware acceptance; use the compatible reviewed release when available. |
| Eight-digit numbers differ | Cancel. Do not approve either side or save the observed certificate. Start a fresh physical pairing attempt and check the intended robot. |
| Pairing expired or rejected | Start again from the physical control. Old numbers and pairing material cannot authorize a later window. |
| Certificate or identity changed | Stop and verify the robot. Discovery cannot replace a stored pin. Use a new physically approved pairing only for an intentional identity/replacement change; never disable certificate checks. |
| Address changed or wrong robot on reconfigure | Reconfigure verifies the new address using the saved pin, credential, and local robot UUID. A changed pin or revoked key requires physical reauthentication; add a separate entry for a different robot. |
| Migration required | Pair one robot directly and explicitly choose the old Jibo device/area mapping. The old cloud socket stays stopped; no fallback is attempted. |
| Legacy cleanup incomplete | After successful local pairing, disconnect the old cloud installation in the Phoenix console when its automatic revocation was offline. |
| Pairing replaced or revoked | Another physical replacement or Forget invalidated the old access. Pair deliberately again; do not run cloned HA entries concurrently. |
| Local connection lost | Check the robot and LAN path. HA reconnects with bounded backoff. Lost work is not queued or replayed. |
| Voice unavailable while connected | Local diagnostics do not establish Phoenix ASR availability. Voice still requires recognized text during a real native wake turn. |
| Request storage error | Fix HA's private request/config storage permissions or corruption, then reload. The integration must fail closed rather than execute without durable deduplication. |
| Robot identity storage error | Use local owner recovery. A paired certificate/identity must not regenerate silently. Do not manually copy another robot's identity or credential. |

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

If removal could not reach Jibo, open **Settings → Home Assistant → Forget** on Jibo and confirm. HA unload/disable alone stops its socket without proving durable revocation. Direct mode stays selected after revocation or Forget; the old cloud home-action path does not reactivate.

Diagnostics should contain bounded status, protocol/version, error codes, anonymous counts, and timing; they should omit credentials, certificate pins, addresses, names, household identifiers, and utterances. Do not share config-entry storage, request ledgers, robot identity files, private captures, or backups. Report the generic error and exact software versions instead.

See [installation and migration](installation.md), [security](security.md), and [pending validation](validation.md).
