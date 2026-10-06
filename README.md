<p align="center">
  <img src="https://raw.githubusercontent.com/Paskooter/phoenix-home-assistant/main/docs/images/hero.svg" width="100%" alt="Someone says “Hey Jibo, turn on the kitchen lights.” Jibo sends the request over a padlocked local connection to Home Assistant, and a lamp lights up.">
</p>

<h1 align="center">Phoenix for Home Assistant</h1>

<p align="center">
  <b>Ask Jibo, and your home does it.</b><br>
  Pair Jibo directly with Home Assistant on your own network. He can switch lights, run scenes, answer questions about your home and read out your announcements.
</p>

<p align="center">
  <a href="https://github.com/Paskooter/phoenix-home-assistant/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/Paskooter/phoenix-home-assistant?include_prereleases&amp;style=flat-square&amp;label=release&amp;color=ff7a3d"></a>
  <a href="https://hacs.xyz/docs/faq/custom_repositories/"><img alt="HACS custom repository" src="https://img.shields.io/badge/HACS-custom-41BDF5?style=flat-square"></a>
  <img alt="Home Assistant 2026.8.1 or newer" src="https://img.shields.io/badge/Home%20Assistant-2026.8.1%2B-18BCF2?style=flat-square">
  <img alt="Jibo BE 13.3.0 and Services 13.0.8" src="https://img.shields.io/badge/Jibo-BE%2013.3.0%20%C2%B7%20Services%2013.0.8-6e7c96?style=flat-square">
  <a href="https://github.com/Paskooter/phoenix-home-assistant/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/Paskooter/phoenix-home-assistant/tests.yml?branch=main&amp;style=flat-square&amp;label=tests"></a>
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Paskooter&amp;repository=phoenix-home-assistant&amp;category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open this repository in HACS in your Home Assistant"></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=phoenix"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Start setting up Phoenix in your Home Assistant"></a>
</p>

<p align="center">
  <a href="#what-jibo-can-do">Features</a> ·
  <a href="#try-saying">Try saying</a> ·
  <a href="#install">Install</a> ·
  <a href="#pair-a-jibo">Pair</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#make-it-yours">Settings</a> ·
  <a href="#announcements">Announcements</a> ·
  <a href="#control-jibo-from-home-assistant">Robot controls</a> ·
  <a href="#upgrading-from-the-cloud-beta">Upgrade</a> ·
  <a href="#remove">Remove</a> ·
  <a href="#documentation">Docs</a>
</p>

> [!NOTE]
> **0.4.0b1 adds single-code setup and opt-in robot controls.** Its software checks passed on HA 2026.8.1 and 2026.9.4, and BE 13.3.0 starts normally on the test robot while preserving its existing pairing. New controls are undergoing physical acceptance. The preceding 0.3.0b3 passed physical pairing, genuine voice-controlled light on/off and all 15 live sensors. [See the exact evidence](docs/validation.md).

## What Jibo can do

<table>
  <tr>
    <td width="50%" valign="top">💡 <b>Lights and switches</b><br>On and off, brightness and supported colors, by name or by room.</td>
    <td width="50%" valign="top">🎬 <b>Scenes and scripts</b><br>Start the ones you've exposed to Assist.</td>
  </tr>
  <tr>
    <td valign="top">📍 <b>Room aware</b><br>Give Jibo an Area, and “the lights here” means the room he's in.</td>
    <td valign="top">❓ <b>Questions</b><br>Ask whether something is on or how warm a room is. Nothing changes.</td>
  </tr>
  <tr>
    <td valign="top">🔁 <b>Follow-ups</b><br>“Turn them off”, up to 30 seconds after a command.</td>
    <td valign="top">🗣️ <b>Your own phrases</b><br>Up to 16 exact phrases, each starting a scene or script.</td>
  </tr>
  <tr>
    <td valign="top">📣 <b>Announcements</b><br>Automations can speak through Jibo, if you allow it.</td>
    <td valign="top">📊 <b>15 robot sensors</b><br>Battery, head touch, temperatures and more, live in Home Assistant.</td>
  </tr>
</table>

## Try saying

Start every request, follow-ups included, with a new “Hey Jibo”.

| To | Say |
| :--- | :--- |
| Switch a light | “Turn on the bedroom light.” · “Turn off the kitchen lights.” |
| Light the room Jibo is in | “Turn on the lights here.” |
| Dim or brighten | “Set the bedroom light brightness to fifty percent.” |
| Change color | “Set the bedroom light to blue.” |
| Flip a switch | “Turn on the garden switch.” |
| Start a scene or script | “Activate the dinner scene.” · “Run the relax script.” |
| Ask about your home | “Are the lights here on?” · “What is the kitchen temperature?” |
| Follow up, within 30 seconds | “Turn them off.” · “Set it to blue.” · “Make them dimmer.” |
| Anything else Assist understands | “Ask Home Assistant to turn on Cooking Lamp.” |

Use the names, aliases and Areas your devices already have in Home Assistant, which works out what you mean.

<details>
<summary><b>How rooms, questions and follow-ups behave</b></summary>
<br>

- **Rooms.** “Here” and “turn on the lights” use the Area you give the Jibo device. Without one, Jibo asks you to set up the room instead of switching the whole home. Naming a device or room still works.
- **Questions** only read states you've exposed to Assist; they never run a conversation agent. Unknown, ambiguous, unavailable or unexposed targets give an error. Temperature and humidity need a matching sensor or climate reading.
- **Follow-ups** reuse the devices from your last successful command, on the same Jibo, for up to 30 seconds. “Dimmer” and “brighter” step brightness by about ten percentage points, and need a usable brightness on every target. Home Assistant checks exposure and capabilities again. A disconnect, restart, error or uncertain result clears the follow-up.
- **Jibo's own commands** stay his. Time, volume, sleep, jokes, weather, cancelling and replies inside a running skill keep working as before.

</details>

## Before you start

| You need | Details |
| :--- | :--- |
| **Home Assistant** | 2026.8.1 or newer; tested on 2026.8.1 and 2026.9.4 |
| **Jibo's software** | BE 13.3.0 and Services 13.0.8 in `home_assistant` or `home_assistant_ssh` mode, on OS 13.0.7 |
| **Your network** | Home Assistant can reach Jibo on local TCP 9443. No port forwarding or public URL |
| **A Phoenix server** | Jibo still uses Phoenix, such as jibo.io, to turn speech into text |

You won't need a Phoenix console code, your jibo.io password or a Home Assistant access token.

## Install

1. In HACS, add `https://github.com/Paskooter/phoenix-home-assistant` as a custom repository, type **Integration**. The **Open in HACS** button above takes you there.
2. Open **Phoenix**, allow beta versions, choose **0.4.0b1** and download it.
3. Restart Home Assistant. Refresh your browser if Phoenix doesn't appear when you add an integration.

<details>
<summary><b>Install by hand instead</b></summary>
<br>

Download `phoenix.zip` from the [latest release](https://github.com/Paskooter/phoenix-home-assistant/releases) and extract it into `custom_components/phoenix/` in your Home Assistant configuration directory, so the manifest ends up at `custom_components/phoenix/manifest.json`. Don't copy the repository root there. Restart Home Assistant.

</details>

## Pair a Jibo

1. **Open the setup form.** In HA, go to **Settings → Devices & services → Add integration → Phoenix**.
2. **Start pairing on Jibo.** Open **Settings → Home Assistant → Start pairing**. His screen shows his address and an eight-digit code valid for 120 seconds.
3. **Enter the address and code in HA.** The port defaults to **9443**. Include leading zeroes. Submit once; both devices finish automatically.
4. **Choose Done on Jibo.** He returns to his face. **Manage** has separate replacement and disconnect confirmation screens.
5. **Choose what he may control.** Assign Jibo an HA Area and expose devices under **Settings → Voice assistants → Expose**. Start with one light.

HA uses Jibo's locally stored nickname or actual four-word native name; a device name you explicitly set in HA takes precedence. Each robot has its own entry and one HA pairing. A deliberate replacement requires **Manage → Replace connection** and confirmation on Jibo.

Existing local links survive an upgrade without pairing again. New pairing and controls require **BE 13.3.0**. A wrong or expired code grants no access; start a new window if needed. Never expose TCP 9443 or mDNS to the Internet.

## How it works

<p align="center">
  <img src="https://raw.githubusercontent.com/Paskooter/phoenix-home-assistant/main/docs/images/how-it-works.svg" width="100%" alt="Phoenix, outside your home network, turns speech into text during a “Hey Jibo” turn. Inside your home network, Jibo sends the request to Home Assistant over a paired, encrypted connection on TCP 9443, and Assist runs it and replies.">
</p>

When you say “Hey Jibo”, Phoenix turns your words into text, as it does for all of Jibo's voice requests. Jibo checks that the request belongs to that voice turn, then sends it straight to Home Assistant, whose Assist runs it. Jibo speaks the reply.

- **The home connection is local.** Home Assistant opens a certificate-pinned TLS connection to Jibo on TCP 9443. Device states, results, pairing credentials and the connection itself stay on that path, and it doesn't depend on Phoenix.
- **Phoenix still hears you.** Voice recognition needs Phoenix, which sees what you say. This doesn't add offline recognition or full transcript privacy. Firmware your operator provides is trusted too.
- **You decide what Jibo can reach:** only what you expose to Assist. Scenes and scripts do whatever you configured them to do.

Read more in [trust and permissions](https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/security.md).

## Make it yours

Everything after pairing lives in Home Assistant.

| To | Go to |
| :--- | :--- |
| Make “here” mean Jibo's room | Give the Jibo device an **Area** |
| Choose what Jibo can control | <kbd>Settings</kbd> › <kbd>Voice assistants</kbd> › <kbd>Expose</kbd> |
| Add your own routine phrases | **Phoenix** › **Configure** |
| Use another conversation agent | **Phoenix** › **Configure** |
| Allow announcements, set quiet hours | **Phoenix** › **Configure** |

**Routine phrases.** Map an exact phrase, such as “reading time”, to a scene or script you've exposed. You can add up to 16. Matching ignores case, extra spaces and final punctuation, but otherwise needs the exact phrase. Home Assistant checks exposure again before starting it, and Jibo says it **started**, without claiming every device's final state.

**Conversation agents.** The default is **Home Assistant (built-in)**, and no LLM is needed. You can choose another installed agent, such as [Jev](https://github.com/Paskooter/ha-conversation-jev); its own settings, permissions and data handling then apply. If the chosen agent goes missing, Jibo reports it as unavailable rather than quietly switching. A slow agent can run past the voice deadline.

## Announcements

Automations can speak through Jibo once you allow it. Turn on **Allow announcements** in **Phoenix** › **Configure**; it's off by default. Optional quiet hours there use Home Assistant's time zone and can cross midnight.

In <kbd>Developer tools</kbd> › <kbd>Actions</kbd>, choose **Notifications: Send a message** and your Jibo's **Announcement** entity, or use it in an automation:

```yaml
action: notify.send_message
target:
  entity_id: notify.jibo_announcement # Choose your actual paired robot's entity.
data:
  message: "Dinner is ready."
```

- Plain text of up to 300 characters, without a title. Jibo uses his current volume; there's no per-message volume.
- Quiet hours, permission turned off, an offline or busy Jibo, or expiry rejects the message. Nothing is queued for later.
- Success means Jibo finished speaking. A lost reply is uncertain and never retried automatically.
- A new “Hey Jibo”, or a supported touch, can stop an announcement. Stopping it doesn't undo anything already done.

<details>
<summary><b>More about announcement permission</b></summary>
<br>

Permission is local to Home Assistant: the older Phoenix console setting doesn't enable it, and there's no second switch on Jibo. Each new connection starts with announcements off until Home Assistant applies your saved option.

</details>

> [!NOTE]
> Direct announcements passed software checks, but physical playback and interruption haven't been retested on this beta. Neither has the connection while Phoenix restarts.

## Control Jibo from Home Assistant

In **Phoenix → Configure**, enable only the controls you want. Screen, ring, audio, sleep, skills and camera each have a separate permission, **off by default**. Controls appear only when the paired firmware advertises support. Their state comes from native observations; an accepted action does not invent a resulting state. See [robot controls](docs/robot-controls.md) for actions and examples.

| Control | What it supports |
| --- | --- |
| Screen text | Plain text up to 300 characters; text/image actions with a 0.1–60 second duration |
| Screen image | A PNG/JPEG from HA’s local Media directory; up to 1 MiB and 1280 × 720 pixels |
| Ring light | RGB color and brightness; owned LED animation expires automatically |
| Speaker | Master volume and local PCM16 WAV playback, pause, resume and stop |
| Sleep | Native sleep and wake handlers; no synthetic wake phrase |
| Start installed skill | Clock, Radio stations, Yoga and Word of the day when installed |
| Stop Home Assistant activity | Stop only activity opened by this integration; available even during owned work |
| Camera preview | Explicit, visible sessions up to 60 seconds, with hatch protection and bounded still-image MJPEG |
| Announcement | Plain speech through Jibo’s familiar voice, with native completion acknowledgement |

Media is uploaded directly from HA over pinned TLS, kept only in robot memory for a short time, and consumed once. Robot controls accept no arbitrary web URLs, shell commands, JavaScript or method names. Touch, a native voice turn, new robot activity, disconnect and permission removal preempt owned screen, ring, audio, skill and camera resources.

Quiet hours apply to announcements, audio controls and audible skills. Ordinary Jibo commands keep their native routing. Opening a camera card does not start capture: use **Camera: Turn on** explicitly. The active session displays a notice on Jibo; opening the hatch or touching his head stops it. This is a short preview, not continuous video, gallery access, or microphone streaming.

### Supported work and next additions

Single-code setup, connection management, all 15 sensors, voice commands, announcements and the controls above are implemented in this release. Hardware evidence is recorded separately in [validation](docs/validation.md).

Weather launch from HA, arbitrary Nimbus execution, full-rate multi-camera video and remote media URLs need additional native command paths and testing. They are research items; no release date or compatibility promise is attached. Jibo’s ordinary spoken weather command remains available.

## Robot sensors

Each paired Jibo adds 15 read-only entities. Their values stay on the local connection.

<details>
<summary><b>See all 15 sensors</b></summary>
<br>

| Entity | Reading |
| :--- | :--- |
| Battery | Percent |
| Battery temperature | Temperature |
| Camera | Idle, active preview, or disabled by the hatch |
| Charging state | Charging, not charging, or not plugged in |
| CPU temperature | Temperature |
| Fan speed | Percent of maximum |
| Hatch state | Open or closed |
| Head touch | On or off, updated the moment he's touched |
| Main board temperature | Temperature |
| Microphone RMS | Sound level in dB; no audio samples |
| Online | Health of the authenticated direct connection |
| Plugged in | Plugged in or unplugged |
| Sleeping | Jibo's own asleep state |
| Speaker volume | Percent; reading it doesn't change the volume |
| System voltage | Volts |

Temperatures arrive in Celsius and show in your preferred unit. A missing or stale reading becomes unavailable within 30 seconds, and a lost connection clears them all. **Camera** reports the preview and hatch status only, with no image stream, and an idle preview doesn't mean Jibo's perception cameras are off.

</details>

## Good to know

- **One request at a time, in English.** Requests joined with “and” or “then” aren't supported.
- **If Jibo can't confirm a result**, the device might have changed anyway. Check it before asking again. Requests have short deadlines, are recorded before they run, and are never queued or replayed after a reconnect or restart.
- **Lights and switches** wait for Home Assistant to report the new state before Jibo answers. Scenes, scripts and other agents report their own kind of completion.
- **Diagnostics** describe the Jibo and Home Assistant connection only. They can't tell you whether Phoenix voice recognition is available, or tell a switched-off Jibo apart from every network problem. They're designed to leave out credentials, pins, addresses, household identifiers and anything said.

## Upgrading from the cloud beta

Links made with the older [0.2.0b2](https://github.com/Paskooter/phoenix-home-assistant/releases/tag/v0.2.0b2) went through Phoenix's cloud connector. After you upgrade, each one shows **migration required**. Its cloud connection stays stopped, and nothing quietly falls back to it.

1. Keep a Home Assistant backup somewhere private. It contains credentials, so don't share it.
2. Update Jibo's software, then reconfigure the old entry. Choose the old Jibo device whose Area to keep, or choose not to copy a room.
3. Pair that Jibo as above. Agent, routine and quiet-hours options carry over; turn announcements on again if you want them.
4. Pair any other Jibo as its own entry.

Once pairing succeeds, Home Assistant tries to revoke the old cloud link and removes its credential. If it couldn't reach Phoenix, disconnect the old link yourself in the Phoenix console. On jibo.io, that's the [Home Assistant page](https://jibo.io/app#/home-assistant).

## Remove

1. While Jibo is reachable, delete his **Phoenix** entry in <kbd>Settings</kbd> › <kbd>Devices & services</kbd>. Home Assistant tries to revoke the pairing and clears its request records.
2. If Home Assistant says Jibo was unreachable, open <kbd>Settings</kbd> › <kbd>Home Assistant</kbd> › <kbd>Manage</kbd> › <kbd>Disconnect</kbd> on Jibo and confirm. Treat access as revoked only after this step, or after Home Assistant confirms it revoked the pairing.
3. Once no Phoenix entries remain, remove the HACS download or manual files and restart Home Assistant.

Disabling an entry only stops its connection; it doesn't revoke the pairing. **Manage → Disconnect** keeps direct mode on and never brings back cloud home control or announcements. Before giving Jibo to someone else, use **Manage → Disconnect** and remove the old entry.

## Documentation

| Guide | Covers |
| :--- | :--- |
| [Installation, upgrade and removal](https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/installation.md) | Every step in detail, including address changes, certificates and replacement |
| [Troubleshooting](https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/troubleshooting.md) | Symptoms and what to check |
| [Trust and permissions](https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/security.md) | What is trusted, stored and revoked |
| [Protocol](https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/protocol.md) | The wire contract between Home Assistant and Jibo |
| [Validation](https://github.com/Paskooter/phoenix-home-assistant/blob/main/docs/validation.md) | Hardware and software results, kept apart, and what's still open |
| [Contributing](https://github.com/Paskooter/phoenix-home-assistant/blob/main/CONTRIBUTING.md) | Running the tests |

Public fixtures use invented devices and identities. Private captures and household storage stay out of Git.

<p align="center"><sub>Part of <a href="https://github.com/Paskooter/phoenix">Phoenix</a>, which brings Jibo back. MIT licensed.</sub></p>
