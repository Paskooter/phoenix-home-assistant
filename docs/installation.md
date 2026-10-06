# Install, pair, upgrade, and remove

These instructions describe the **0.4.0b1 direct beta candidate**. See [validation](validation.md) for exact software and hardware evidence. Existing local pairings survive an upgrade; new pairing requires the single-code firmware.

## Before installing

- Home Assistant **2026.8.1 or newer**; software checks passed on actual 2026.8.1 and 2026.9.4.
- Jibo with **BE 13.3.0** and **Services 13.0.8**, using `home_assistant` or `home_assistant_ssh` mode. OS **13.0.7** remains the baseline.
- HA must reach the robot's local TCP **9443**. Enter its host manually in the flow. Any mDNS/UDP **5353** discovery result is an address suggestion, not authorization.
- Keep a private HA backup before upgrading. A backup contains credentials and must not be shared.

Use the reviewed robot update instructions for the compatible release. No router port forwarding, public HA URL, Phoenix connection code, or HA access token is needed for local pairing. Phoenix recognition remains necessary for voice turns.

## HACS or manual installation

In HACS, add `https://github.com/Paskooter/phoenix-home-assistant` under **Custom repositories**, category **Integration**. Find **Phoenix**, enable beta/prerelease versions, select **0.4.0b1**, and download it. Restart HA and refresh the browser if the integration picker has not refreshed.

For a manual installation, use the reviewed release's `phoenix.zip`. Extract its payload directly into your HA configuration directory's `custom_components/phoenix/`, so the final path is `custom_components/phoenix/manifest.json`. Do not copy the repository root into that directory. Restart HA.

## Pair one Jibo

1. In HA, open **Settings → Devices & services → Add integration → Phoenix**.
2. On Jibo, open **Settings → Home Assistant → Start pairing**. His screen shows his local address and an **eight-digit code**, valid for **120 seconds**.
3. Enter that address and all eight digits in HA, then submit. The port defaults to **9443**. Both devices finish automatically; no comparison/Approve round trip is required.
4. Jibo shows **Connected**. Select **Done** to return to his face. **Manage** offers separate confirmation screens for replacing or disconnecting the connection.
5. In HA, assign Jibo to an **Area** and expose home targets under **Settings → Voice assistants → Expose**. In **Phoenix → Configure**, enable only the robot controls you want.

The code is verified by a mutual authenticated exchange and is not transmitted as plain text. A wrong code, an expired window or a different robot does not create access. Leading zeroes count. If the window expires, start a new window and enter the newly displayed code.

One HA pairing belongs to each robot. Add a separate entry for each Jibo. HA uses the locally stored nickname or actual four-word native name; a device name you explicitly set in HA takes precedence. Cloning HA credentials is not a supported way to share pairing.

## Upgrade an existing local connection

Back up HA privately. Update the integration through HACS and restart HA. Apply the compatible robot release. The saved local identity, credential, certificate pin, room assignment and entity IDs remain valid. You do **not** need to pair again simply to upgrade. New robot controls have independent permissions and start off.

If the address changes, use **Reconfigure**. Reauthentication starts a fresh physical-code flow only if the stored identity or credential can no longer be verified.

## Upgrade a cloud connection

Upgrade compatible robot software before relying on the direct integration. When HA loads an old protocol-1 entry with the direct integration, it enters **migration required** and its old cloud socket stays stopped. Ordinary HA options are retained. There is no automatic cloud fallback.

Open the entry's reconfiguration or migration prompt:

1. Select the particular old Jibo device whose area should be retained, or explicitly choose no room copy. HA must not guess this association: its local robot UUID differs from the cloud binding.
2. Enter the robot's local host and port, then complete the physical single-code pairing.
3. Check the resulting Jibo device's area and retained conversation-agent, routine, and quiet-hours options. Local announcements require a separate explicit opt-in.
4. Give each remaining legacy robot its own separately paired entry.

Only after local pairing succeeds does HA attempt old cloud installation revocation and remove the old credential. If Phoenix is unreachable during that cleanup, follow the notification to **Disconnect** the old installation in the [Phoenix console](https://jibo.io/app#/home-assistant). That manual cleanup concerns the legacy installation; it does not reconnect the local entry to the cloud.

A failed or cancelled local pairing must not revoke the working old installation prematurely or create local operational access. The migration-required entry remains stopped until the owner completes pairing.

## Addresses, certificate changes, and replacement

A robot address may change while its local identity remains the same. Use **Reconfigure** to update its host: HA checks the saved certificate pin, credential, and local robot UUID before saving the address. A changed identity or credential requires physical pairing again through reauthentication. A different robot must use a separate entry.

An ordinary saved connection fails if a different certificate or local identity appears. Address discovery cannot replace its pin. An intentional new pairing establishes trust through a fresh code displayed on the robot; it must not accept a changed certificate merely because the address looks familiar.

For a deliberate replacement HA installation, choose **Manage → Replace connection** on Jibo, confirm, then enter his newly displayed code in HA. Replacement revokes the previous robot pairing, closes its old socket, and clears follow-up state. Do not delete storage or disable TLS checks to fix an unexpected identity change.

A corrupted paired identity requires local owner recovery. **Manage → Disconnect** revokes a pairing and keeps the robot identity; it does not repair corrupted identity storage. Never delete storage or silently regenerate an identity as a repair.

## Announcements

Enable **Allow announcements** in the paired entry's local **Configure** options when you want HA automations to speak. It defaults off; there is no second portal or robot permission toggle. A new robot session starts disabled until authenticated HA preferences apply the saved option. Configure optional quiet hours there too. HA's time zone determines them; an interval may cross midnight.

Messages are plain text, at most 300 characters, without titles or volume overrides. They use Jibo's current volume. Busy, disconnected, expired, or rejected work is never queued for later. The direct path passed software checks; physical playback, interruption and operation during Phoenix restarts have not been retested for this beta.

## Disable or remove

Disabling the entry or unloading the integration stops HA's local socket and clears transient follow-up state. It does not durably revoke the robot's credential or undo device actions.

For durable removal:

1. Keep the robot reachable, then remove its Phoenix entry in **Settings → Devices & services**. HA attempts authenticated local pairing revocation and removes its request ledger.
2. If HA reports that the robot was unreachable, open **Settings → Home Assistant → Manage → Disconnect** on Jibo, then confirm **Disconnect**. Consider access revoked only after that step or confirmed authenticated revocation.
3. Remove the HACS/manual download only when no Phoenix entries remain, then restart HA.

Physical Disconnect works without Phoenix or HA availability. Direct mode stays selected after Disconnect, revocation, reconnect, and restart; these events do not restore cloud home commands or announcements. Legacy installations left by an offline migration also need their explicit console cleanup. Removing Phoenix does not change your Assist exposure settings or other HA integrations.

Before transferring Jibo to another owner, complete physical Disconnect and remove the old HA entry. Changing a Phoenix account association does not transfer or erase local pairing; some existing transfer and reconnect paths preserve the native credentials. A new owner must create a new physical pairing.

See [troubleshooting](troubleshooting.md) for pairing, storage, and connection failures.
