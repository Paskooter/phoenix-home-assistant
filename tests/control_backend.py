"""Invented control endpoint with real loopback TLS; no owner or robot data."""

import io
import json
import time
import wave
from uuid import uuid4

from aiohttp import WSMsgType, web
from PIL import Image

from tests.direct_backend import BASE, CAPABILITIES, SyntheticLocalRobot

FEATURES = [
    "screen_text",
    "screen_image",
    "ring_color",
    "speaker_volume",
    "audio_playback",
    "sleep_control",
    "installed_skills",
    "camera_streaming",
]


def invented_image(content_type="image/png"):
    output = io.BytesIO()
    Image.new("RGB", (8, 8), (15, 30, 45)).save(output, format="JPEG" if content_type == "image/jpeg" else "PNG")
    return output.getvalue()


def invented_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 800)
    return output.getvalue()


class SyntheticControlRobot(SyntheticLocalRobot):
    """Synthetic state changes are explicit native observations, never HA optimism."""

    def __init__(self, directory):
        super().__init__(directory)
        self.features = list(FEATURES)
        self.firmware_version = "13.3.0"
        self.enabled = []
        self.denied_groups = set()
        self.control_calls = []
        self.uploads = []
        self.media = {}
        self.snapshot_calls = 0
        self.hatch_closed = True
        self.publish_state = True
        self.hold_actions = set()
        self.held = []
        self.upload_redirect = None
        self.camera_expiry = None
        self.initial_state_age_ms = 0
        self.values = {
            "speaker_volume_percent": 42,
            "screen_active": False,
            "screen_text": None,
            "screen_image_id": None,
            "ring_active": False,
            "ring_rgb": None,
            "audio_state": "idle",
            "audio_media_id": None,
            "sleeping": False,
            "installed_skills": [
                {"id": "@invented/clock", "name": "Invented Clock"},
                {"id": "@invented/exercise", "name": "Invented Exercise"},
            ],
            "active_skill_id": None,
            "camera_active": False,
            "camera_expires_at_monotonic_ms": None,
        }

    async def start(self):
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_get(BASE + "/identity", self._identity)
        app.router.add_post(BASE + "/pair/{step}", self._pair)
        app.router.add_delete(BASE + "/pairing", self._revoke)
        app.router.add_get(BASE + "/connect", self._connect)
        app.router.add_post(BASE + "/media", self._media)
        app.router.add_get(BASE + "/camera.jpg", self._camera)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0, ssl_context=self.context)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.host = "127.0.0.1"
        return self

    async def _connect(self, request):
        if not self._authorized(request):
            return web.Response(status=401)
        await self.disconnect(4002)
        socket = web.WebSocketResponse(max_msg_size=8192)
        await socket.prepare(request)
        self.socket = socket
        self.connections += 1
        self.session_id = str(uuid4())
        self.enabled = []
        await self.send(
            {
                "type": "welcome",
                "robot_id": self.robot_id,
                "server_time_ms": self.now_ms(),
                "capabilities": [*CAPABILITIES, "robot_controls"],
            }
        )
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                frame = json.loads(message.data)
                self.frames.append(frame)
                assert (
                    frame["v"] == 2
                    and frame["session_id"] == self.session_id
                    and frame["generation"] == self.generation
                )
                if frame["type"] == "ready":
                    assert frame["agent"] == "home_assistant"
                elif frame["type"] == "preferences":
                    self.preferences = frame
                    self.announcements_enabled = frame["announcements_enabled"]
                    self.enabled = [
                        group for group in frame.get("controls_enabled", []) if group not in self.denied_groups
                    ]
                    await self.roster()
                    await self.controls_state(age_ms=self.initial_state_age_ms)
                elif frame["type"] == "robot_action":
                    await self._action(frame)
                elif frame["type"] in ("accepted", "result"):
                    if (
                        frame["type"] == "result"
                        and (future := self.waiters.get(frame["request_id"]))
                        and not future.done()
                    ):
                        future.set_result(frame["result"])
                else:
                    raise AssertionError("Unexpected synthetic control frame")
        finally:
            if self.socket is socket:
                self.socket = None
        return socket

    async def roster(self):
        await self.send(
            {
                "type": "roster",
                "robots": [
                    {
                        "robot_id": self.robot_id,
                        "name": self.name,
                        "firmware_version": self.firmware_version,
                        "online": self.online,
                        "busy": self.busy,
                        "announcements_allowed": self.announcements_enabled,
                        "announcements_supported": self.supported,
                        "controls_supported": self.features,
                        "controls_enabled": self.enabled,
                    }
                ],
            }
        )

    async def controls_state(self, *, age_ms=0, values=None):
        await self.send(
            {
                "type": "controls_state",
                "robot_id": self.robot_id,
                "observed_at_ms": self.now_ms() - age_ms,
                "state": {
                    "observed_at_monotonic_ms": time.monotonic() * 1000,
                    "values": self.values if values is None else values,
                },
            }
        )

    async def _result(self, frame, outcome="success", code=None):
        await self.send(
            {
                "type": "action_result",
                "request_id": frame["request_id"],
                "robot_id": self.robot_id,
                "result": {"outcome": outcome, "speech": "", **({"code": code} if code else {})},
            }
        )

    async def _action(self, frame):
        self.control_calls.append(frame)
        action, payload = frame["action"], frame.get("payload", {})
        if action in self.hold_actions:
            self.held.append(frame)
            return
        if action == "display_text":
            self.values.update(screen_active=True, screen_text=payload["text"], screen_image_id=None)
        elif action == "display_image":
            assert payload["media_id"] in self.media
            self.values.update(screen_active=True, screen_text=None, screen_image_id=payload["media_id"])
        elif action == "clear_screen":
            self.values.update(screen_active=False, screen_text=None, screen_image_id=None)
        elif action == "set_ring_color":
            self.values.update(ring_active=True, ring_rgb=payload["rgb"])
        elif action == "ring_off":
            self.values.update(ring_active=False, ring_rgb=None)
        elif action == "set_volume":
            self.values["speaker_volume_percent"] = payload["volume_percent"]
        elif action == "play_audio":
            assert payload["media_id"] in self.media
            self.values.update(audio_state="playing", audio_media_id=payload["media_id"])
        elif action == "pause_audio":
            self.values["audio_state"] = "paused"
        elif action == "resume_audio":
            self.values["audio_state"] = "playing"
        elif action == "stop_audio":
            self.values.update(audio_state="idle", audio_media_id=None)
        elif action in ("sleep", "wake"):
            self.values["sleeping"] = action == "sleep"
        elif action == "run_skill":
            assert any(skill["id"] == payload["skill_id"] for skill in self.values["installed_skills"])
            self.values["active_skill_id"] = payload["skill_id"]
        elif action == "start_camera":
            if not self.hatch_closed:
                await self._result(frame, "error", "camera_unavailable")
                return
            self.values.update(
                camera_active=True, camera_expires_at_monotonic_ms=time.monotonic() * 1000 + payload["duration_ms"]
            )
        elif action == "stop_camera":
            self.values.update(camera_active=False, camera_expires_at_monotonic_ms=None)
        elif action == "stop":
            for previous in self.held:
                await self._result(previous, "uncertain", "interrupted")
            self.held.clear()
            self.values.update(
                screen_active=False,
                screen_text=None,
                screen_image_id=None,
                ring_active=False,
                ring_rgb=None,
                audio_state="idle",
                audio_media_id=None,
                active_skill_id=None,
                camera_active=False,
            )
        elif action != "weather":
            raise AssertionError("Unexpected synthetic native action")
        if self.publish_state:
            await self.controls_state()
        await self._result(frame)

    async def _media(self, request):
        if not self._authorized(request):
            return web.Response(status=401)
        if self.upload_redirect:
            raise web.HTTPFound(location=self.upload_redirect)
        if (request.content_type.startswith("image/") and "screen" not in self.enabled) or (
            request.content_type == "audio/wav" and "audio" not in self.enabled
        ):
            return web.Response(status=403)
        data = await request.read()
        media_id = str(uuid4())
        self.media[media_id] = data
        self.uploads.append((media_id, request.content_type, data))
        return web.json_response(
            {"media_id": media_id, "content_type": request.content_type, "size": len(data)}, status=201
        )

    async def _camera(self, request):
        self.snapshot_calls += 1
        if not self._authorized(request):
            return web.Response(status=401)
        if not self.values["camera_active"] or "camera" not in self.enabled or not self.hatch_closed:
            return web.Response(status=409)
        return web.Response(body=invented_image("image/jpeg"), content_type="image/jpeg")
