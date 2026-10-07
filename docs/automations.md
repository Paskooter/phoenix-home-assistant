# Ready-made Jibo automations

These three blueprints add small, useful routines to a paired Jibo. Import one,
choose your entities and save the automation. Each automation stays under your
control in Home Assistant; disable or delete it there at any time.

They use the existing direct Phoenix connection and require Home Assistant
**2026.8.1 or newer**. Announcements need the **Allow announcements** permission.
The sleep schedule also needs the **Sleep** switch provided by compatible robot
software, with sleep controls enabled. See [installation](installation.md) and
[robot controls](robot-controls.md) for those requirements.

## Install a blueprint

1. Choose an **Import** link below and open your own Home Assistant.
2. Check the blueprint description, then choose **Import blueprint**.
3. Choose **Create automation**, select your Jibo's actual entities, and save.

If an Import link cannot open your HA, go to **Settings → Automations & scenes →
Blueprints → Import blueprint** and paste the corresponding **Source** link.
HACS installs the integration; these blueprints are imported separately.

| Automation | Add it | Source |
| --- | --- | --- |
| Announce a device state change | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FPaskooter%2Fphoenix-home-assistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fphoenix%2Fstate_announcement.yaml) | [YAML](../blueprints/automation/phoenix/state_announcement.yaml) |
| Remind me to plug Jibo in | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FPaskooter%2Fphoenix-home-assistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fphoenix%2Fbattery_reminder.yaml) | [YAML](../blueprints/automation/phoenix/battery_reminder.yaml) |
| Sleep and wake on a schedule | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FPaskooter%2Fphoenix-home-assistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fphoenix%2Fsleep_schedule.yaml) | [YAML](../blueprints/automation/phoenix/sleep_schedule.yaml) |

To install manually, copy a YAML file into
`config/blueprints/automation/phoenix/` and reload automations. It then appears
under **Blueprints**. The [Home Assistant blueprint guide](https://www.home-assistant.io/docs/automation/using_blueprints/)
explains importing, updating and creating more than one automation from a blueprint.

## Announce a device state change

Choose a device, its previous and new states, Jibo's **Announcement** entity,
and a short message. For example, an invented door sensor could change from
`off` to `on` and say “The side door is open.” A device can also have states
such as `running` and `finished`; use the actual values shown in
**Developer tools → States**, which can differ from dashboard labels.

The optional wait requires the new state to last that many seconds. A door
that closes during the wait cancels the announcement. The default cooldown is
60 seconds; repeated transitions during that cooldown are dropped. A failed
announcement also starts the cooldown.

The device must make the exact chosen transition. Starting HA with the door
already open, changing only an attribute, or recovering from `unavailable`
does not announce it. Keep messages to 1–300 characters of plain text.

## Remind me to plug Jibo in

Select **Battery**, **Plugged in**, and **Announcement** from the same Jibo.
By default, he asks to be plugged in when his battery falls below 20% while
unplugged, or when unplugged while already below that limit. He stays quiet
while plugged in. The threshold and message are editable.

This is one reminder per qualifying event, with a default 30-minute cooldown.
It does not repeat every 30 minutes while the battery remains low. Missing
readings and connection recovery do not become a low-battery event. Check the
actual battery sensor if you need the current reading.

## Sleep and wake on a schedule

Enable sleep controls in **Phoenix → Configure**. Select Jibo's **Sleep**
switch, a sleep time, a different wake time, and the days you want.

The default sleeps at 22:00 and wakes at 08:00 every day, using **Home Assistant's
time zone**. Days apply to each event separately: choosing Monday–Friday means
Friday's sleep event runs and Saturday's wake event does not. Choose all days
for the same routine every night.

These are native sleep and wake actions. If Jibo is already in the requested
state, nothing is sent. A missed time while HA or Jibo is offline is skipped;
reconnecting never wakes him or puts him to sleep unexpectedly. Identical sleep
and wake times disable both events until you choose different times.

## If a routine stays quiet

Check **Settings → Automations & scenes** to confirm the automation is enabled,
then open its **Traces**. Check Jibo's connection and the required permission in
**Phoenix → Configure**. The **Announcement status** sensor explains whether
he is ready, busy, offline, within quiet hours, or needs permission.

Announcements respect the integration's quiet hours and current volume.
Unavailable, busy, rejected or uncertain actions are never retried or held for
later. If the robot becomes busy between the trigger and the action, that
attempt can appear as a service error in the trace. Sleep and wake actions use
their own explicit schedule, independently of announcement quiet hours.

The state-change wait and cooldown are local automation timers. Restarting HA
or reloading automations clears them. No past trigger is replayed. These
blueprints have been exercised through Home Assistant's real automation engine
and synthetic paired TLS endpoints; that software validation does not claim a
new physical robot acceptance result.
