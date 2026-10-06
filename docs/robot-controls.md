# Control Jibo locally

These controls require BE **13.3.0** and the **0.4.0b1** integration. Enable the
permissions you want in **Settings → Devices & services → Phoenix → Configure**.
Each permission starts off. Keep this page's examples pointed at your actual
Jibo entities; the names below are examples.

## Screen

Use the **Screen text** entity to show plain text. An empty value clears the
integration's screen. The `phoenix.show_text`, `phoenix.show_image` and
`phoenix.clear_screen` actions target that same entity.

```yaml
action: phoenix.show_text
target:
  entity_id: text.jibo_screen_text
data:
  text: "Dinner is ready."
  duration_ms: 10000
```

Upload an image through Home Assistant's **Media** browser, then choose its
`media-source://media_source/` identifier:

```yaml
action: phoenix.show_image
target:
  entity_id: text.jibo_screen_text
data:
  media_content_id: "media-source://media_source/local/example.png"
  duration_ms: 10000
```

Text is limited to 300 characters. Text and image durations range from 100 to
60000 milliseconds and default to 10000. Images must be PNG or JPEG, at most
1 MiB and 1280 × 720 pixels. Jibo displays literal text, without interpreting
HTML or speech markup. A touch or new robot activity closes this owned view.

## Ring, volume and audio

The **Ring light** entity supports Home Assistant's normal RGB and brightness
controls. Its LED-only animation expires within 60 seconds; it moves no joints.
Turning it off releases the integration's animation.

The **Speaker** entity sets Jibo's master volume and plays local PCM16 WAV files.
Use **Media player: Play media**, select this speaker and choose a local Media
file. Audio must be mono or stereo, 8–48 kHz, no more than 8 MiB or 60 seconds.
Pause, resume and stop affect only this integration's audio. MP3, OGG, web URLs
and remote media sources are not supported.

```yaml
action: media_player.play_media
target:
  entity_id: media_player.jibo_speaker
data:
  media_content_type: audio/wav
  media_content_id: "media-source://media_source/local/example.wav"
```

Quiet hours block new audio controls, announcements and audible skill launches.
Stop, pause and cleanup remain available. Volume is a persistent master-volume
change; a disconnect does not restore a guessed previous volume.

## Sleep, skills and stop

The **Sleep** switch calls Jibo's actual sleep and wake handlers. It does not
generate a pretend “Hey Jibo” event. Availability and state follow native
observations; waking cannot authorize a home command.

**Start installed skill** lists supported skills actually present on this Jibo:
Clock, Radio stations, Yoga and Word of the day. Selection confirms launch,
without claiming a skill has finished. An owned launch expires within 60
seconds. Jibo's regular speech can still request weather; there is no HA weather
launch or arbitrary Nimbus/script-execution action in this release.

**Stop Home Assistant activity** releases only resources opened by this
integration. It stays available during owned activity and with permissions off.
It does not stop somebody else's active robot skill or undo a home-device action.

## Camera video

Enable **Allow live camera sessions**, then use **Camera: Turn on**
with Jibo's **Camera** entity. Every session lasts at most 60 seconds.
Jibo must be idle with his hatch closed and a fresh native hatch reading. His
screen visibly says **Home Assistant camera is on**.

The camera entity receives native continuous VP8/WebM video over the paired
TLS connection. Home Assistant uses its FFmpeg decoder to display an MJPEG
feed at up to 15 frames per second. JPEG snapshots remain available. Native
video is video-only: no microphone audio, stereo feed or gallery access is
provided. Video is never saved on Jibo by this integration.

For a dashboard live view, add a Picture entity card and choose **Live** for
its camera view. Use your Camera entity's actual ID; for example:

```yaml
type: picture-entity
entity: camera.jibo_camera
camera_view: live
show_name: true
show_state: true
```

Opening a dashboard or requesting a picture cannot activate capture. Touching
Jibo's head, opening his hatch, a new voice turn or native activity, stopping the
camera, disconnecting or removing permission stops the owned session. A lost
connection makes its state unavailable rather than assuming capture stopped.

## Results and privacy

Controls have request IDs and deadlines. Both sides record admission before
execution; reconnect and restart never replay an action. A lost reply means the
result is uncertain: check the robot before issuing another request.

Images and audio travel directly from HA to Jibo over authenticated pinned TLS,
remain only in robot memory, expire after 60 seconds if unused, and are consumed
once. Disconnect and revocation erase unused uploads. No robot URL fetcher,
gallery browser, household dump or remote execution endpoint is exposed.

See [validation](validation.md) for the distinction between isolated software
checks, the previous voice/sensor hardware release and this release's hardware
acceptance.
