# Trust and permissions

This document covers the **0.3.0b3 direct candidate** with BE 13.2.2 and Services 13.0.8. Direct hardware and release acceptance are pending. The published 0.2.0b2 cloud connector has a different trust boundary.

## Physical pairing and the local connection

Home Assistant connects to Jibo over TLS on the LAN. The robot creates a separate local identity, certificate, and credential; it does not reuse a Phoenix account credential. Home Assistant pins the SHA-256 fingerprint of the complete peer certificate after the physical pairing exchange succeeds.

An address suggestion, including one from discovery, cannot authorize a robot, replace a saved identity, or change a certificate pin. Initial pairing uses an eight-digit short authentication string (SAS): compare the complete number on both screens, approve on the robot, and confirm in HA. Cancel if it differs or you did not start the physical pairing window. The number is for comparison and is not a bearer credential.

One robot accepts one HA pairing. Physical replacement advances its persistent pairing generation, closes the old connection, and clears follow-up state before granting the replacement access. A pinned certificate change or corrupted paired identity fails closed; it must not silently create a new identity or trust another endpoint.

Keep TCP 9443 and mDNS/UDP 5353 on the intended local network. Port forwarding and a public HA URL are unnecessary.

## Voice and firmware trust

A native, single-use wake admission is required for each home command or state query. Cloud metadata, an unsolicited result, or a routing hint cannot open that admission or extend its lifetime. Commands and results travel between the paired robot and HA rather than through a cloud command socket.

Phoenix supplies trusted recognized text during an admitted voice turn and can observe that utterance. Operator-provided firmware and its existing fixed trust configuration remain trusted. Local pairing authenticates the robot–HA connection; voice still needs Phoenix and does not gain offline recognition or full transcript privacy.

Connection health and opt-in local announcements are intended to operate during a Phoenix restart. A functioning LAN session does not prove that cloud recognition is available. This independence remains a direct candidate validation requirement.

## Permissions in Home Assistant

- Expose only the devices you intend to make available through Assist. Scenes and scripts execute the behavior you configured locally.
- Assign the paired Jibo device to an HA area for room-relative commands. Neither cloud text nor a claimed room in a request assigns that area.
- Local state questions read exposed states without invoking a conversation agent or device service. Routine phrases and follow-ups recheck exposure and their current target capabilities.
- Selecting another conversation agent also trusts that agent's permissions, provider calls, and data handling. The connector does not turn arbitrary agent behavior into a service sandbox.
- Announcements require HA-local **Allow announcements** opt-in, default off. The endpoint resets permission to false for every session and accepts the setting only through authenticated preferences. There is no second portal or robot toggle. HA applies quiet hours and uses the robot's current volume.

Disconnecting or cancelling stops future admission where possible. It cannot undo a device action that has already executed.

## Credentials, storage, and no replay

HA stores the paired credential and certificate pin privately in its config-entry storage. The robot keeps its local certificate key, identity, pairing generation, and credential in private owner storage. The candidate requires atomic durable writes and rejection of unsafe symlink or corrupted identity state. A paired identity is never regenerated automatically after an error.

HA remembers request IDs durably before accepting or executing commands. The robot persists announcement admission before speech. These records are deduplication tombstones, not delivery queues. Expired, disconnected, or uncertain work is never replayed after reconnect or restart. A lost result is reported as uncertain.

Downloaded diagnostics should omit credentials, pins, addresses, names, household/device/request identifiers, routine phrases, and utterances. Do not share HA's `.storage/core.config_entries`, request storage, robot identity storage, or a private backup. Treat backups and cloned HA configurations as containing live credentials.

## Revocation and migration

Removing the HA entry attempts authenticated local revocation. If the robot is unreachable, use **Settings → Home Assistant → Forget** on Jibo and confirm Forget. Disabling HA stops the socket but does not erase the robot's pairing. A physical replacement pairing revokes the old credential. Forget retains the robot's identity; corrupted identity storage needs owner recovery. Direct mode stays selected after Forget, revocation, reconnect, and restart, so these events do not restore a cloud home-action path.

Local pairing is independent of Phoenix account ownership. A confirmed change in Jibo's locally stored native credentials revokes its pairing; temporarily unreadable credentials pause access without erasing the key. A cloud account reassignment can preserve those credentials and does not revoke or transfer the local pairing. Before handing Jibo to another owner, use **Settings → Home Assistant → Forget**, confirm, and remove the old HA entry. Do not assume a cloud transfer or reset alone erased the pairing.

An upgraded legacy entry remains migration-required with its cloud socket stopped until physical pairing succeeds. Old cloud credentials cannot authenticate the local endpoint. Migration retains agent, routine, and quiet-hours options, resets announcements to off, and requires an explicit old robot device/area selection; cloud and local UUIDs cannot be assumed to identify the same device.

After local pairing succeeds, HA attempts old cloud installation revocation and removes the old credential. Offline revocation needs manual **Disconnect** in the Phoenix console. This cleanup does not authorize cloud fallback.

See [installation](installation.md), [troubleshooting](troubleshooting.md), and [protocol](protocol.md).
