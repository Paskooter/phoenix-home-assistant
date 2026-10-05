# Install, pair, upgrade, and remove

These instructions describe the **0.3.0b3 direct candidate**. Direct hardware validation and release review are pending. They apply to compatible reviewed packages when released; the published 0.2.0b2 cloud package cannot create this local pairing.

## Before installing

- Home Assistant **2026.8.1 or newer**; software checks passed on actual 2026.8.1 and 2026.9.4.
- Jibo with **BE 13.2.2** and **Services 13.0.8**, using `home_assistant` or `home_assistant_ssh` mode. OS **13.0.7** remains the baseline.
- HA must reach the robot's local TCP **9443**. Enter its host manually in the candidate flow. Any mDNS/UDP **5353** discovery result is an address suggestion, not authorization.
- Keep a private HA backup before upgrading. A backup contains credentials and must not be shared.

Use the reviewed robot update instructions for the compatible release. No router port forwarding, public HA URL, Phoenix connection code, or HA access token is needed for local pairing. Phoenix recognition remains necessary for voice turns.

## HACS or manual installation

In HACS, add `https://github.com/Paskooter/phoenix-home-assistant` under **Custom repositories**, category **Integration**. Find **Phoenix**, enable beta/prerelease versions, select the compatible direct release when available, and download it. Restart HA and refresh the browser if the integration picker has not refreshed.

For a manual installation, use the reviewed release's `phoenix.zip`. Extract its payload directly into your HA configuration directory's `custom_components/phoenix/`, so the final path is `custom_components/phoenix/manifest.json`. Do not copy the repository root into that directory. Restart HA.

## Pair one Jibo

1. On Jibo, open **Settings → Home Assistant → Start pairing**. Use the **Host** shown there. The window expires after **120 seconds** and admits one candidate.
2. In HA, open **Settings → Devices & services → Add integration → Phoenix**. Enter its local host and port, normally **9443**.
3. Compare the complete **eight-digit** number on Jibo and in the HA confirmation form. The host, discovery name, and initial certificate alone are not approval.
4. If the numbers match, approve the live candidate on Jibo and confirm the match and approval in HA. If they differ, cancel both sides and open a fresh physical window.
5. After successful pairing, assign the Jibo device to an HA **Area**. Expose intended targets under **Settings → Voice assistants → Expose** and start with a single light.

The integration saves the robot's local identity, paired credential, and certificate pin. One HA pairing belongs to each robot; add another entry for another Jibo. Running a cloned HA configuration against the same robot is not a supported way to share pairing.

If the window expires, restart from the robot's physical control. Do not reuse a displayed number or bypass the certificate comparison. A fresh attempt uses new pairing material.

## Upgrade a cloud connection

Upgrade compatible robot software before relying on the direct integration. When HA loads an old protocol-1 entry with the direct integration, it enters **migration required** and its old cloud socket stays stopped. Ordinary HA options are retained. There is no automatic cloud fallback.

Open the entry's reconfiguration or migration prompt:

1. Select the particular old Jibo device whose area should be retained, or explicitly choose no room copy. HA must not guess this association: its local robot UUID differs from the cloud binding.
2. Enter the robot's local host and port, then complete the physical pairing and eight-digit comparison.
3. Check the resulting Jibo device's area and retained conversation-agent, routine, and quiet-hours options. Local announcements require a separate explicit opt-in.
4. Give each remaining legacy robot its own separately paired entry.

Only after local pairing succeeds does HA attempt old cloud installation revocation and remove the old credential. If Phoenix is unreachable during that cleanup, follow the notification to **Disconnect** the old installation in the [Phoenix console](https://jibo.io/app#/home-assistant). That manual cleanup concerns the legacy installation; it does not reconnect the local entry to the cloud.

A failed or cancelled local pairing must not revoke the working old installation prematurely or create local operational access. The migration-required entry remains stopped until the owner completes pairing.

## Addresses, certificate changes, and replacement

A robot address may change while its local identity remains the same. Use **Reconfigure** to update its host: HA checks the saved certificate pin, credential, and local robot UUID before saving the address. A changed identity or credential requires physical pairing again through reauthentication. A different robot must use a separate entry.

An ordinary saved connection fails if a different certificate or local identity appears. Address discovery cannot replace its pin. An intentional new pairing establishes trust through the complete physical comparison again; it must not accept a changed certificate merely because the address looks familiar.

For a deliberate new pairing or replacement HA installation, open the physical pairing window again and compare a fresh number. Replacement revokes the previous robot pairing, closes its old socket, and clears follow-up state. Do not delete storage or disable TLS checks to fix an unexpected identity change.

A corrupted paired identity requires local owner recovery. **Forget** revokes a pairing and keeps the robot identity; it does not repair corrupted identity storage. Never delete storage or silently regenerate an identity as a repair.

## Announcements

Enable **Allow announcements** in the paired entry's local **Configure** options when you want HA automations to speak. It defaults off; there is no second portal or robot permission toggle. A new robot session starts disabled until authenticated HA preferences apply the saved option. Configure optional quiet hours there too. HA's time zone determines them; an interval may cross midnight.

Messages are plain text, at most 300 characters, without titles or volume overrides. They use Jibo's current volume. Busy, disconnected, expired, or rejected work is never queued for later. Local announcement and connection-health operation during Phoenix restarts still needs candidate validation.

## Disable or remove

Disabling the entry or unloading the integration stops HA's local socket and clears transient follow-up state. It does not durably revoke the robot's credential or undo device actions.

For durable removal:

1. Keep the robot reachable, then remove its Phoenix entry in **Settings → Devices & services**. HA attempts authenticated local pairing revocation and removes its request ledger.
2. If HA reports that the robot was unreachable, open **Settings → Home Assistant → Forget** on Jibo, then confirm **Forget**. Consider access revoked only after that step or confirmed authenticated revocation.
3. Remove the HACS/manual download only when no Phoenix entries remain, then restart HA.

Physical Forget works without Phoenix or HA availability. Direct mode stays selected after Forget, revocation, reconnect, and restart; these events do not restore cloud home commands or announcements. Legacy installations left by an offline migration also need their explicit console cleanup. Removing Phoenix does not change your Assist exposure settings or other HA integrations.

Before transferring Jibo to another owner, complete physical Forget and remove the old HA entry. Changing a Phoenix account association does not transfer or erase local pairing; some existing transfer and reconnect paths preserve the native credentials. A new owner must create a new physical pairing.

See [troubleshooting](troubleshooting.md) for pairing, storage, and connection failures.
