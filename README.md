# Phoenix for Home Assistant

Let Jibo control devices exposed to Home Assistant Assist. This beta uses Home Assistant's built-in conversation agent, with no LLM requirement. Home Assistant connects **outbound over authenticated TLS WebSocket** to Phoenix. No port forwarding, public Home Assistant URL, or paid remote-access subscription is needed.

Beta: **0.1.0b4**. Home Assistant **2026.8.1 or newer**; English commands. Compatibility tests run against 2026.8.1 and 2026.9.4. The previous 0.1.0b3 owner-installed HA 2026.8.1 connector controlled one approved physical light from a BE 13.0.2 Jibo and completed native spoken replies through production, using supplied ASR text. Direct and explicit on/off commands passed. A fresh human-spoken wake phrase and microphone check remains pending; see [validation evidence](docs/validation.md).

## Install through HACS

1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/Paskooter/phoenix-home-assistant`, category **Integration**.
3. Find **Phoenix**, select **Download**, enable beta/prerelease versions when choosing a version, and select **0.1.0b4**.
4. Restart Home Assistant. Refresh the browser if Phoenix does not appear in the integration picker.

For a manual installation, download `phoenix.zip` from the [beta release](https://github.com/Paskooter/phoenix-home-assistant/releases/tag/v0.1.0b4). Create `custom_components/phoenix/` inside your Home Assistant configuration directory, then extract the archive's files directly into it. The resulting path must be `custom_components/phoenix/manifest.json`. Restart Home Assistant. Do not copy the repository's entire root into `custom_components/phoenix`.

## Link your household

1. Sign in to the [Phoenix console](https://jibo.io/app#/home-assistant) with the account that owns your Jibo.
2. Open **Home Assistant**, name the installation, select the robots to enable, and generate a connection code. Codes expire after ten minutes and work once.
3. In Home Assistant, open **Settings → Devices & services → Add integration → Phoenix**. Leave the server URL as `https://jibo.io`, and enter the code.
4. Under **Settings → Voice assistants → Expose**, expose the lights, switches, scenes, and scripts you want Assist to control. Set names, aliases, and areas there as usual. Start with a single light.
5. Confirm **Connected** in the Phoenix console and the integration's connection diagnostic entity. Then try a command below.

A self-hosted Phoenix installation can use its own HTTPS origin instead. It needs a publicly trusted certificate, the server changes described in [Phoenix's operator guide](https://github.com/Paskooter/phoenix/blob/main/docs/HOME-ASSISTANT.md), and working WebSocket proxying. Home Assistant's URL stays private.

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

The first beta handles one command per turn. It does not translate every utterance into a home command. Volume, sleep, time, jokes, weather, cancellation, and answers inside active skills retain their existing Jibo routing. A new wake phrase starts a global turn. Existing Hue routing remains available on robots that have not opted in. There are no Home Assistant-to-Jibo controls in this release.

## Optional Jev or another Assist agent

Open **Settings → Devices & services → Phoenix → Configure**, choose an installed conversation agent, and save. The existing owner link is kept. The default remains **Home Assistant (built-in)**. A removed or renamed selected agent returns an unavailable error; it never silently switches to another agent.

For OpenRouter-powered Jev with a Grok fallback, install [Paskooter/ha-conversation-jev](https://github.com/Paskooter/ha-conversation-jev) and follow its [owner setup guide](https://github.com/Paskooter/ha-conversation-jev/blob/main/docs/setup.md). Select **Jev Assist** in Phoenix's Configure form. Jev's provider key and Grok login are local HA settings. Jibo keeps its existing recognition and voice.

The command phrase tests above describe Home Assistant's **built-in** agent. A custom agent has its own capabilities, provider data and latency. The existing 7.5-second command deadline still applies; slow model work can expire, and actions are not replayed.

## Connection and uncertain results

The integration exposes a **Connection** binary sensor and **Connection status** sensor under its device's diagnostic entities. Status is also available in Phoenix. It reconnects automatically with bounded backoff after a network interruption.

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

Diagnostics contain connection state, protocol/version, error code, and task counts. They omit credentials, server URLs, installation IDs, robot IDs, and utterances. Do not share Home Assistant's `.storage/core.config_entries` or Phoenix's account store: these contain private connection information.

## Upgrade, move, and remove

Before upgrading, keep a Home Assistant backup. Download the new version in HACS and restart HA. The connector socket and background tasks belong to the config entry and stop during unload/restart. Already issued actions are not replayed. Version 1 config entries need no data migration for this beta.

To move servers or relink, disconnect the old installation in Phoenix, create a new code, then use **Reconfigure** on the integration (or its reauthentication prompt). A new code creates installation-specific credentials. HA never asks for your jibo.io password or a HA access token.

To remove access immediately, choose **Disconnect** in the Phoenix console. Remove the Phoenix integration under **Settings → Devices & services**, then remove the HACS download and restart. HA also attempts server revocation during removal. If Phoenix was unreachable, follow the notification to disconnect it in the console. Device exposure in Assist remains your HA setting.

## Development and evidence

See [CONTRIBUTING.md](CONTRIBUTING.md) for reproducible tests, [protocol.md](docs/protocol.md) for the wire contract, and [release evidence](docs/validation.md) for the distinction between isolated tests and physical evidence. All public fixtures use invented devices and identities.

API references: [HA conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/), [config entries](https://developers.home-assistant.io/docs/config_entries_index/), [Assist exposure](https://www.home-assistant.io/voice_control/voice_remote_expose_devices/).
