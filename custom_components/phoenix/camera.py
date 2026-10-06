"""Privacy-aware snapshots and native continuous video through authenticated HA."""

import asyncio

from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback

from .camera_stream import MAX_VIDEO_VIEWERS, CameraVideoSession
from .control_entity import PhoenixControlEntity, add_control_entities
from .controls_api import async_camera_image


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(entry, async_add_entities, {"camera_streaming": PhoenixCamera})


class PhoenixCamera(PhoenixControlEntity, Camera):
    """A dashboard reads an active capture; camera.turn_on starts a 60-second session."""

    _attr_name = "Camera preview"
    _attr_supported_features = CameraEntityFeature.ON_OFF
    _attr_brand = "Jibo"
    _attr_model = "Jibo"

    def __init__(self, client, robot_id):
        Camera.__init__(self)
        PhoenixControlEntity.__init__(self, client, robot_id, "camera", "camera_streaming")
        self._video_sessions: set[CameraVideoSession] = set()
        self._video_close_tasks: set[asyncio.Task] = set()
        self._video_stopping = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.client.subscribe(self._video_state_changed))
        self.async_on_remove(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop_video))

    @callback
    def _video_state_changed(self) -> None:
        if self.available and self.is_on:
            return
        for session in tuple(self._video_sessions):
            if session.stop_requested:
                continue
            session.stop_requested = True
            task = self.hass.async_create_task(session.async_close(), "Close Jibo camera video")
            self._video_close_tasks.add(task)
            task.add_done_callback(self._video_close_tasks.discard)

    async def _async_stop_video(self, _event=None) -> None:
        self._video_stopping = True
        await asyncio.gather(*(session.async_close() for session in tuple(self._video_sessions)))
        if self._video_close_tasks:
            await asyncio.gather(*tuple(self._video_close_tasks), return_exceptions=True)

    async def async_will_remove_from_hass(self) -> None:
        await self._async_stop_video()
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        return super().available and self.observed("camera_active") is not None

    @property
    def is_on(self):
        return self.observed("camera_active") is True

    @property
    def is_streaming(self):
        return self.observed("camera_active") is True

    async def async_turn_on(self) -> None:
        await self.client.async_control(self.robot_id, "start_camera", {"duration_ms": 60_000})

    async def async_turn_off(self) -> None:
        await self.client.async_control(self.robot_id, "stop_camera")

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        return await async_camera_image(self.client, self.robot_id)

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        """Decode continuous native WebM; this method never calls start_camera."""
        if self._video_stopping or not self.available or not self.is_on:
            raise web.HTTPConflict(text="Jibo's camera is inactive. Start a camera session explicitly.")
        if len(self._video_sessions) >= MAX_VIDEO_VIEWERS:
            raise web.HTTPTooManyRequests(text="Jibo's camera already has two viewers.")
        session = CameraVideoSession(self.client, self.robot_id)
        self._video_sessions.add(session)
        try:
            return await session.async_stream(request)
        finally:
            await session.async_close()
            self._video_sessions.discard(session)

    async def handle_async_still_stream(self, request: web.Request, interval: float) -> web.StreamResponse:
        """Even HA's interval URL consumes continuous native video, never photo polling."""
        return await self.handle_async_mjpeg_stream(request)
