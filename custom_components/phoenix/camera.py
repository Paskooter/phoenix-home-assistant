"""Privacy-aware native snapshots and bounded still-image MJPEG streaming."""

from homeassistant.components.camera import Camera, CameraEntityFeature

from .control_entity import PhoenixControlEntity, add_control_entities
from .controls_api import async_camera_image


async def async_setup_entry(hass, entry, async_add_entities):
    add_control_entities(entry, async_add_entities, {"camera_streaming": PhoenixCamera})


class PhoenixCamera(PhoenixControlEntity, Camera):
    """A dashboard reads an active capture; camera.turn_on starts a 60-second session."""

    _attr_name = "Camera preview"
    _attr_supported_features = CameraEntityFeature.ON_OFF
    _attr_frame_interval = 2.0
    _attr_brand = "Jibo"
    _attr_model = "Jibo"

    def __init__(self, client, robot_id):
        Camera.__init__(self)
        PhoenixControlEntity.__init__(self, client, robot_id, "camera", "camera_streaming")

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
