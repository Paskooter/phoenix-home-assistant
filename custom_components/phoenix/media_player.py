"""Native speaker volume and bounded local WAV playback."""

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)

from .control_entity import PhoenixControlEntity, add_control_entities
from .controls_api import AUDIO_TYPES, async_upload_media


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(
        entry, async_add_entities, {"speaker_volume": PhoenixSpeaker, "audio_playback": PhoenixSpeaker}
    )


class PhoenixSpeaker(PhoenixControlEntity, MediaPlayerEntity):
    """No queue, implicit retries, mute emulation or optimistic playback state."""

    _attr_name = "Speaker"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_volume_step = 0.05

    def __init__(self, client, robot_id):
        super().__init__(client, robot_id, "speaker", "speaker_volume")

    @property
    def available(self) -> bool:
        return any(
            self.client.control_available(self.robot_id, feature) for feature in ("speaker_volume", "audio_playback")
        )

    @property
    def supported_features(self):
        features = MediaPlayerEntityFeature(0)
        if self.robot and "speaker_volume" in self.robot.controls_supported:
            features |= MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_STEP
        if self.robot and "audio_playback" in self.robot.controls_supported:
            features |= (
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.PLAY
                | MediaPlayerEntityFeature.PAUSE
                | MediaPlayerEntityFeature.STOP
                | MediaPlayerEntityFeature.BROWSE_MEDIA
            )
        return features

    @property
    def state(self):
        return {
            "idle": MediaPlayerState.IDLE,
            "loading": MediaPlayerState.BUFFERING,
            "playing": MediaPlayerState.PLAYING,
            "paused": MediaPlayerState.PAUSED,
        }.get(self.observed("audio_state"))

    @property
    def volume_level(self):
        value = self.observed("speaker_volume_percent")
        return value / 100 if value is not None else None

    @property
    def media_content_id(self):
        return self.observed("audio_media_id")

    @property
    def media_content_type(self):
        return MediaType.MUSIC if self.observed("audio_media_id") is not None else None

    async def async_set_volume_level(self, volume: float) -> None:
        await self.client.async_control(self.robot_id, "set_volume", {"volume_percent": volume * 100})

    async def async_volume_up(self) -> None:
        if self.volume_level is None:
            self.client._control_error("invalid_payload")
        await self.async_set_volume_level(min(1, self.volume_level + self.volume_step))

    async def async_volume_down(self) -> None:
        if self.volume_level is None:
            self.client._control_error("invalid_payload")
        await self.async_set_volume_level(max(0, self.volume_level - self.volume_step))

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        if (
            media_type not in (*AUDIO_TYPES, MediaType.MUSIC)
            or kwargs.get("enqueue") not in (None, MediaPlayerEnqueue.REPLACE)
            or kwargs.get("announce")
        ):
            self.client._control_error("invalid_payload")
        uploaded = await async_upload_media(self.client, self.robot_id, media_id, "audio", self.entity_id)
        await self.client.async_control(self.robot_id, "play_audio", {"media_id": uploaded})

    async def async_media_stop(self) -> None:
        await self.client.async_control(self.robot_id, "stop_audio")

    async def async_media_pause(self) -> None:
        await self.client.async_control(self.robot_id, "pause_audio")

    async def async_media_play(self) -> None:
        await self.client.async_control(self.robot_id, "resume_audio")

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        if media_content_id and not media_content_id.startswith("media-source://media_source"):
            self.client._control_error("invalid_payload")
        return await media_source.async_browse_media(
            self.hass,
            media_content_id or "media-source://media_source",
            content_filter=lambda item: item.media_content_type in AUDIO_TYPES,
        )
