# Trust and permissions

Phoenix is trusted to deliver Jibo's voice commands. Home Assistant initiates
the TLS connection; it does not publish its address or give Phoenix a Home
Assistant access token. The installation credential authenticates that
connection. This protects against unrelated callers, but an operator who
controls the running Phoenix service can originate command frames.

This release does not provide end-to-end robot-to-Home-Assistant authentication
or encryption. Phoenix receives the recognized command text and the returned
speech. Its server-side identity checks prevent another household or a forged
client context from using your installation; they do not exclude the server
operator from the trust boundary. A connection password or TLS certificate
alone cannot establish that a person actually spoke a command to Jibo.

## Controls in Home Assistant

- Use **Settings → Voice assistants → Expose** to choose the devices available
  to the built-in Assist agent. Exposed scenes and scripts execute the behavior
  you have configured locally.
- Robot room context, read-only state queries, follow-up target checks and
  routine selection are evaluated in Home Assistant. Queries do not call an
  executing conversation agent. Routine shortcuts recheck exposure immediately
  before calling the selected scene or script.
- Choosing another conversation agent deliberately trusts its behavior and
  providers. That agent's permissions and data handling also apply; the
  connector does not turn an arbitrary third-party agent into a restricted
  service sandbox.
- Disable or remove the Phoenix integration in Home Assistant to stop its
  connection independently of Phoenix. An action already started may have
  executed; disconnecting does not undo it.

Home control requires both Home Assistant and Phoenix to be reachable. The
current connector has no direct LAN robot connection or offline voice mode.
Connection and robot diagnostics reflect their respective cloud paths; a
Phoenix interruption is not independent proof that a robot lost local power or
network access.

## Jibo announcements

Announcements need a compatible native robot receiver and an owner-enabled
installation permission in Phoenix. They are disabled by default. Home
Assistant checks its configured quiet hours before issuing an announcement,
which uses Jibo's current volume. These controls govern requests from this integration; they do
not authenticate a server operator independently to the robot.

The action ledger is not a delivery queue. Request identifiers are persisted
before dispatch, expired work is discarded, and reconnects never replay an
announcement. Success means native spoken completion was acknowledged. A lost
acknowledgement produces uncertainty rather than a retry.

## Private information

The Home Assistant config entry contains a private installation credential.
Phoenix stores its verifier and household/robot bindings. Diagnostics omit
credentials, installation and robot identifiers, server URLs and utterances.
Do not share Home Assistant's config-entry storage or Phoenix's Account store.

For an immediate owner revocation, choose **Disconnect** in the Phoenix
console and disable or remove the integration locally. Relinking uses a new
expiring, single-use code and a new installation credential.
