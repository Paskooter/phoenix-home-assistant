# Phoenix for Home Assistant

Let Jibo control devices exposed to Home Assistant Assist. This beta uses Home Assistant's built-in conversation agent, with no LLM requirement. Home Assistant connects **outbound over authenticated TLS WebSocket** to Phoenix. No port forwarding, public Home Assistant URL, or paid remote-access subscription is needed.

Next beta candidate: **0.2.0b2**. Published beta: **[0.2.0b1](https://github.com/Paskooter/phoenix-home-assistant/releases/tag/v0.2.0b1)**. Home Assistant **2026.8.1 or newer**; English commands. Compatibility tests use actual HA 2026.8.1 and 2026.9.4. Home control keeps Jibo's existing recognition and voice. Native announcements require the corrected receiver in **BE 13.1.1**; physical receiver acceptance is pending. An approved regression on BE 13.0.2 confirmed one physical light's on/off states and native spoken replies through the owner's connector with supplied ASR text. Microphone recognition remains unverified. See [validation evidence](docs/validation.md) for measured results and remaining checks.

Beta 0.2.0b2 corrects the minimum firmware shown by announcement diagnostics and errors after a BE 13.1.0 receiver initialization failure. Each robot's **Announcement status** diagnostic sensor keeps local readiness and the minimum receiver version visible while the Announcement entity is unavailable. The published 0.2.0b1 archive stays unchanged and uses the same protocol as 0.2.0b2. An existing 0.2.0b1 installation remains compatible with BE 13.1.1 and needs no immediate HA reinstall; its older diagnostic label can still show 13.1.0 and unavailable entities can omit the guidance.

## Install through HACS

1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/Paskooter/phoenix-home-assistant`, category **Integration**.
3. Find **Phoenix**, select **Download**, enable beta/prerelease versions when choosing a version, and select **0.2.0b1**.
4. Restart Home Assistant. Refresh the browser if Phoenix does not appear in the integration picker.

For a manual installation, download `phoenix.zip` from the [0.2.0b1 release page](https://github.com/Paskooter/phoenix-home-assistant/releases/tag/v0.2.0b1). Create `custom_components/phoenix/` inside your Home Assistant configuration directory, then extract the archive's files directly into it. The resulting path must be `custom_components/phoenix/manifest.json`. Restart Home Assistant. Do not copy the repository's entire root into `custom_components/phoenix`.

## Link your household

1. Sign in to the [Phoenix console](https://jibo.io/app#/home-assistant) with the account that owns your Jibo.
2. Open **Home Assistant**, name the installation, select the robots to enable, and generate a connection code. Codes expire after ten minutes and work once.
3. In Home Assistant, open **Settings → Devices & services → Add integration → Phoenix**. Leave the server URL as `https://jibo.io`, and enter the code.
4. Under **Settings → Voice assistants → Expose**, expose the lights, switches, scenes, and scripts you want Assist to control. Set names, aliases, and areas there as usual. Start with a single light.
5. Confirm **Connected** in the Phoenix console and the integration's connection diagnostic entity. Then try a command below.

A self-hosted Phoenix installation can use its own HTTPS origin instead. It needs a publicly trusted certificate, the server changes described in [Phoenix's operator guide](https://github.com/Paskooter/phoenix/blob/main/docs/HOME-ASSISTANT.md), and working WebSocket proxying. Home Assistant's URL stays private.

Phoenix is trusted to deliver your voice commands. TLS and installation credentials protect the connection from unrelated callers; they do not prevent an operator who controls the running Phoenix server from originating commands. The built-in agent's Assist exposure limits available devices. See [trust and permissions](docs/security.md) for the exact boundary and local disconnect controls.

## Commands

These phrases have been exercised through the real Home Assistant conversation agent with synthetic devices, including its returned speech. Use your own entity and area names. Say “Hey Jibo” before the command on a robot.

| Purpose | Example |
| --- | --- |
| Named light | Turn on the bedroom light. |
| Room lights | Turn on the kitchen lights. |
| Light off | Turn off the kitchen lights. |
| Brightness | Set the bedroom light brightness to fifty percent. |
| Supported color | Set bedroom light to blue. |
| Named switch | Turn on the garden switch. |
| Scene | Activate the dinner scene. |
| Script | Run relax script. |
| Explicit invocation | Ask Home Assistant to turn on Cooking Lamp. |

Home Assistant resolves names, aliases, areas, available features, and supported sentences. A brightness-only light cannot change color. Direct phrases require a light/lamp/switch noun, a scene/script suffix, or a recognized existing lighting intent. Use **“ask Home Assistant to…”** for custom Assist sentences or aliases that do not fit those forms. Such custom sentences execute whatever you configured locally; expose scripts deliberately.

The connector handles one command per turn. Volume, sleep, time, jokes, weather, cancellation, and answers inside active skills retain their existing Jibo routing. A new wake phrase starts a global turn. Existing Hue routing remains available on robots that have not opted in.

## Rooms, questions, and follow-ups

Open **Settings → Devices & services → Phoenix**, select each Jibo device, and assign its **Area** in Home Assistant. Then “turn on the lights” or “turn on the lights here” targets that room. A robot without an assigned area asks for room setup; it does not expand a generic light command to the whole home. Explicit names such as “kitchen lights” remain usable.

Supported local questions include **“are the kitchen lights on?”**, **“are the lights here on?”**, and **“what is the kitchen temperature?”**. They read only Assist-exposed HA states and never invoke an executing conversation agent, even if Jev or Grok is selected. Temperature/humidity readings need an exposed sensor with the corresponding device class or a climate entity reporting that measurement. Unknown, ambiguous, or unavailable targets return an error instead of a guessed answer.

For up to **30 seconds** after a successful home turn, the same robot can refer to its resolved targets: **“turn them off”**, **“set it brightness to 25 percent”**, **“set it to blue”**, or **“make it dimmer”** / **“make them brighter”**. Relative brightness changes by about ten percentage points and requires every target to be an on light with a usable brightness reading. Levels clamp at off/full brightness. Supported capabilities and current Assist exposure are checked again before dispatch; each requested level must be confirmed. Each follow-up still begins with “Hey Jibo.” Context is separate for each robot and clears on errors, partial or uncertain results, disconnect, restart, and agent changes. A state question can continue with **“and in the bedroom?”**; it remains read-only.

## Routine phrases

In **Phoenix → Configure**, enter an exact **Add routine phrase** and select an Assist-exposed scene or script. For example, map **“reading time”** to your dinner scene, then say “Hey Jibo, reading time.” Reopen Configure to add another phrase or remove existing ones. You can save up to 16 bounded phrases.

Matching ignores case, whitespace, and final punctuation. It does not infer similar phrases. Home Assistant rechecks exposure before activation and reports the routine as **started**, without claiming every device reached its final state. Existing native Jibo commands keep their routing; use a distinctive phrase that does not collide with one.

## Announcements from Home Assistant

Announcements are off by default. Enable **Allow announcements** for the linked installation in the [Phoenix console](https://jibo.io/app#/home-assistant). The robot needs the corrected native receiver in **BE 13.1.1**. The BE 13.1.0 receiver candidate failed initialization; BE 13.1.1 hardware acceptance is pending. Home control, rooms, questions, and routines do not require the announcement receiver. In 0.2.0b2, the **Announcement status** sensor reports `firmware_required` until receiver support is available, even while the Announcement entity is unavailable.

Open **Phoenix → Configure** to choose optional quiet hours. Quiet hours use HA's time zone and can cross midnight. Announcements use Jibo's current volume. Per-announcement volume is not supported by the tested native speech engine.

In **Developer tools → Actions**, select **Notifications: Send a message**, select the Jibo's **Announcement** entity, and enter a message. Use that same action in an automation:

```yaml
action: notify.send_message
target:
  entity_id: notify.jibo_announcement # Choose your actual Phoenix entity.
data:
  message: "Dinner is ready."
```

Messages are plain text, at most 300 characters, without a title. Quiet hours, offline or busy robots, disabled permission, and missing firmware produce a clear error; nothing is queued. A notification completes only after native spoken completion is acknowledged. If acknowledgement is lost, the result is uncertain and the message is never retried. A new voice turn can cancel an announcement; if native stop cannot be confirmed, new speech remains blocked until recovery proves the previous speech stopped.

## Optional Jev or another Assist agent

Open **Settings → Devices & services → Phoenix → Configure**, choose an installed conversation agent, and save. The existing owner link is kept. The default remains **Home Assistant (built-in)**. A removed or renamed selected agent returns an unavailable error; it never silently switches to another agent.

For OpenRouter-powered Jev with a Grok fallback, install [Paskooter/ha-conversation-jev](https://github.com/Paskooter/ha-conversation-jev) **0.3.0b1** and follow its [owner setup guide](https://github.com/Paskooter/ha-conversation-jev/blob/main/docs/setup.md). Select **Jev Assist** in Phoenix's Configure form. Jev's provider key and Grok login are local HA settings. Jibo keeps its existing recognition and voice. Jev adds local color, spelled brightness, exact scene/script, room, and follow-up paths; classifier and fallback provider calls remain subject to the voice deadline.

The command phrase tests above describe Home Assistant's **built-in** agent. A custom agent has its own capabilities, provider data and latency. The existing 7.5-second command deadline still applies; slow model work can expire, and actions are not replayed.

## Connection and uncertain results

The integration exposes **Connection** and **Connection status** diagnostics. Each Jibo device also exposes **Robot connection**, **Announcement status**, **Last response**, **Response latency**, and **Conversation agent**. These omit utterances and household identifiers. Robot connection describes the verified Phoenix path; it is not independent LAN or power monitoring. Status is also available in Phoenix. The connector reconnects automatically with bounded backoff after a network interruption.

Actions are never queued for a reconnect or automatically retried. A request that expires before execution is discarded. Request IDs are remembered before executing and persisted without command text. Duplicate work after reconnect or restart is not executed again.

If the response is lost, an action might already have happened. Jibo says he **could not confirm the result**. Check the device's actual state before trying again. Partial success is reported as partial, unknown targets use Home Assistant's response, and an offline connector reports offline rather than success.

For built-in light and switch on/off intents, the connector waits for Home Assistant's resolved targets to report the requested state within the command deadline. It issues no additional service call. If confirmation never arrives, the outcome is uncertain. Scenes, scripts, and custom intents retain their own Home Assistant completion semantics.

## Troubleshooting

- **Invalid code:** generate a fresh code. If a linking response was lost, disconnect the orphan installation in Phoenix first. The credential is delivered only once.
- **Disconnected:** check Home Assistant's Internet access, DNS, TLS trust, and the Phoenix URL. For self-hosted servers, check the dedicated WebSocket proxy location. Connector redirects are rejected; configure the final HTTPS origin directly. Reconnection takes up to about a minute after repeated failures.
- **Relink required:** the credential was revoked or the selected robot's ownership changed. Disconnect the old installation in Phoenix, generate a new code, and complete Home Assistant's reauthentication prompt.
- **Connection replaced:** the same installation credential was used by another running instance. Stop the duplicate and reload, or disconnect and relink. Do not run two HA copies with a cloned Phoenix config entry.
- **Command not understood:** expose the target to Assist, check its name/alias/area and available features, and try the text in Home Assistant's built-in Assist agent. Then try the explicit invocation. The built-in agent is selected by default. For Jev or another installed agent, open Phoenix → Configure and select it explicitly. Changing the default Assist pipeline does not change Jibo’s selection.
- **Unsupported protocol:** update the server and integration to compatible releases. This beta uses connector protocol version 1.
- **Request storage error:** fix Home Assistant's storage permissions or corruption, then reload. The connector fails closed when it cannot preserve request deduplication.
- **Room not configured:** assign the Jibo device to an HA area, or name the target explicitly.
- **No follow-up context:** name the device again. Target memory is deliberately short and clears after interruptions or inconclusive results.
- **Announcement unavailable:** check the robot's **Announcement status** sensor, installation permission, robot connection, and corrected BE 13.1.1 receiver. Quiet-hours rejection is local and never queues a later announcement.

Diagnostics contain connection state, protocol/version, error code, and task counts. They omit credentials, server URLs, installation IDs, robot IDs, and utterances. Do not share Home Assistant's `.storage/core.config_entries` or Phoenix's account store: these contain private connection information.

## Upgrade, move, and remove

Before upgrading, keep a Home Assistant backup. Update the Phoenix server before enabling the new features, download the integration in HACS, and restart HA. Existing version 1 links need no new connection code. Assign robot areas and routine phrases locally; announcements require separate opt-in and compatible firmware. The connector socket and background tasks stop during unload/restart. Already issued actions are not replayed.

To move servers or relink, disconnect the old installation in Phoenix, create a new code, then use **Reconfigure** on the integration (or its reauthentication prompt). A new code creates installation-specific credentials. HA never asks for your jibo.io password or a HA access token.

To remove access immediately, choose **Disconnect** in the Phoenix console. Remove the Phoenix integration under **Settings → Devices & services**, then remove the HACS download and restart. HA also attempts server revocation during removal. If Phoenix was unreachable, follow the notification to disconnect it in the console. Device exposure in Assist remains your HA setting.

## Release scope

The six requested additions are implemented in this beta. Its release evidence distinguishes actual isolated HA checks from physical robot checks.

| Addition | Owner experience |
| --- | --- |
| Jibo announcements from automations | Native announcement entities, installation opt-in and local quiet hours, using Jibo's current volume. Requires corrected BE 13.1.1; physical receiver acceptance is pending. |
| Robot room context | Assign each Jibo to a Home Assistant area so “turn on the lights” can refer to that room. |
| Questions about the home | Ask about the states of Assist-exposed devices, using a route that reads state without changing devices. |
| Brief follow-up context | Refer to the previous home command with phrases such as “make it dimmer”; keep context separate for each robot and expire it after a short period. |
| Owner-selected routine phrases | Invoke explicitly selected scenes or scripts with a short phrase such as “start movie night,” while preserving ordinary Jibo commands. |
| Faster commands and robot diagnostics | Expand Jev's local paths for colors, scenes, and scripts, and show each robot's last result, selected agent, and response time in Home Assistant. |

Household isolation, Assist exposure, revocation, deadlines, honest uncertain outcomes, and no replay remain release requirements. This release keeps Phoenix speech recognition and Jibo's familiar voice. It has no direct LAN connection or operator-excluding cryptographic authorization; see [trust and permissions](docs/security.md).

## Development and evidence

See [CONTRIBUTING.md](CONTRIBUTING.md) for reproducible tests, [protocol.md](docs/protocol.md) for the wire contract, and [release evidence](docs/validation.md) for the distinction between isolated tests and physical evidence. All public fixtures use invented devices and identities.

API references: [HA conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/), [config entries](https://developers.home-assistant.io/docs/config_entries_index/), [Assist exposure](https://www.home-assistant.io/voice_control/voice_remote_expose_devices/).
