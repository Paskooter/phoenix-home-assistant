# Trust and permissions

Home Assistant connects directly to a physically paired Jibo over certificate-pinned TLS on the LAN. Phoenix does not relay this connection. Pairing keys, robot telemetry, home-device state, returned results and uploaded media remain on the HA–Jibo path. No public HA URL, router port forwarding or paid remote-access service is required.

## What is trusted

Owners trust the installed robot firmware and Phoenix speech recognition. Phoenix sees the utterance and supplies recognized text during a native voice turn. This release does not claim offline recognition or private speech transcription. An optional HA conversation agent has its own provider permissions and privacy policy.

Jibo admits a home request only after the supported native wake/turn sequence. Unsolicited cloud home-skill launches, client household context, routing hints and mimic turns cannot create that admission. HA does not accept an operator credential or a cloud-origin connector command. The direct connection and sensor health do not depend on a Phoenix socket staying online.

This boundary assumes the installed firmware and recognition path are trusted. It does not protect against owner-installed malicious firmware or an administrator with root access to the robot or HA. It avoids giving the Phoenix connector operator an independent standing credential to the home.

## Physical-code pairing

On Jibo, **Start pairing** opens a two-minute window and displays an eight-digit single-use code. Enter it and Jibo's address in HA. SRP-6a with the 3072-bit group and SHA-512 mutually authenticates that code without sending the code across HTTP. The exchange binds the robot identity, certificate fingerprint, generation, nonce and both parties' ephemeral values. Both sides derive the operational credential; it is not returned as an unauthenticated bearer secret.

A maximum of five attempts is admitted in a physical window; additional spacing and rate limits apply. The candidate expires after 30 seconds and cannot extend the physical window. A successful durable commit clears the code and replaces the old credential. Lost completion acknowledgement can be recovered through a read-only authenticated status proof. Recovery cannot claim a different robot or extend pairing. New pairing never downgrades to the old comparison protocol.

The initial TLS certificate probe grants no access. Its fingerprint becomes trusted only after successful code authentication. Ordinary requests use the saved pin, credential, local robot identity and generation. A changed certificate is rejected before a credential reaches that peer. Reconfigure verifies the existing pairing instead of trusting a discovery name or a new address blindly.

## Voice targets and robot controls

HA's existing Assist exposure controls determine which home entities are available. The built-in `home_assistant` agent is explicitly selected by default. State questions use exposed local states. Short follow-ups recheck exposure and capabilities; errors, disconnect and restart clear context. HA processing can execute actions, so ordinary Jibo utterances are classified before dispatch rather than probing HA with every sentence.

Announcements, screen, ring, audio, sleep, installed skills and camera are independent local opt-ins, all off by default. Each connection starts with permissions off until authenticated HA preferences arrive. Native support must also be advertised. Removal of permission stops owned work and discards unused uploads.

Controls use a closed action schema. They accept no shell command, JavaScript, arbitrary SDK method or robot-fetched URL. Images and WAV files must come from HA's configured local media directories: symlinks, path escapes, redirects, web URLs and remote sources are rejected. Bounded uploads use pinned TLS, remain in memory, expire if unused and are consumed once.

Camera capture requires an explicitly started session, a visible notice, idle native state and a fresh closed-hatch reading. A session expires within 60 seconds. Opening a card never starts capture. The supported preview path does not save images to the gallery and exports no microphone audio. Touch, hatch opening, a new voice turn, native preemption, disconnect and revocation stop owned resources.

## Failure and revocation

Both sides save bounded request admission before execution. IDs, deadlines and durable tombstones prevent duplicate delivery, reconnect or restart from replaying actions. A lost response is **uncertain**; neither side retries the action or claims definite success/failure. Native cleanup that lacks stop proof remains quarantined rather than advertising a safe idle state.

Removing an HA entry attempts local revocation and deletes its request ledger. If Jibo is unreachable, use **Settings → Home Assistant → Manage → Disconnect** and confirm on the robot. Disabling an entry only stops its socket; it is not durable revocation. Replacing a connection requires a physical confirmation and a new code. Native ownership changes reject the old binding and remove access. Disconnecting never restores the legacy cloud home route.

Keep TCP 9443 and mDNS inside your local network. A private HA backup includes pairing credentials and must not be published. Diagnostics omit keys, pins, addresses, household identities, media and utterances. Public tests use invented identities and devices.
