"""Continuous synthetic WebM through real HA camera HTTP routes and ffmpeg."""

import asyncio
import shutil
from contextlib import suppress

import pytest
from aiohttp import ClientSession, MultipartReader, web
from aiohttp.test_utils import TestServer
from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from custom_components.phoenix.camera_stream import MAX_VIDEO_VIEWERS
from tests.control_backend import SyntheticControlRobot
from tests.direct_backend import BASE, linked_entry, wait_for
from tests.test_controls import enable_controls, entity


class SyntheticVideoRobot(SyntheticControlRobot):
    """A moving VP8 test pattern, with actual credential and pinned loopback TLS."""

    def __init__(self, directory, video):
        super().__init__(directory)
        self.video = video
        self.video_requests = 0
        self.video_authorized = []
        self.active_video_connections = 0
        self.video_status = 200
        self.video_content_type = "video/webm"
        self.video_redirect = None

    async def start(self):
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_get(BASE + "/identity", self._identity)
        app.router.add_post(BASE + "/pair/{step}", self._pair)
        app.router.add_delete(BASE + "/pairing", self._revoke)
        app.router.add_get(BASE + "/connect", self._connect)
        app.router.add_get(BASE + "/camera.jpg", self._camera)
        app.router.add_get(BASE + "/camera.webm", self._video)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0, ssl_context=self.context)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.host = "127.0.0.1"
        return self

    async def _video(self, request):
        self.video_requests += 1
        self.video_authorized.append(self._authorized(request))
        if not self._authorized(request):
            return web.Response(status=401)
        if self.video_redirect:
            raise web.HTTPFound(location=self.video_redirect)
        if not self.values["camera_active"] or not self.hatch_closed or "camera" not in self.enabled:
            return web.Response(status=409)
        if self.video_status != 200:
            return web.Response(status=self.video_status)
        response = web.StreamResponse(headers={"Content-Type": self.video_content_type})
        await response.prepare(request)
        self.active_video_connections += 1
        try:
            for offset in range(0, len(self.video), 1024):
                if (
                    request.transport is None
                    or request.transport.is_closing()
                    or not self.values["camera_active"]
                    or not self.hatch_closed
                ):
                    break
                await response.write(self.video[offset : offset + 1024])
                await asyncio.sleep(0.01)
            while (
                self.values["camera_active"]
                and self.hatch_closed
                and "camera" in self.enabled
                and request.transport is not None
                and not request.transport.is_closing()
            ):
                await asyncio.sleep(0.05)
            with suppress(ConnectionError, RuntimeError):
                await response.write_eof()
        except ConnectionError:
            pass
        finally:
            self.active_video_connections -= 1
        return response


@pytest.fixture
async def native_video():
    binary = shutil.which("ffmpeg")
    assert binary, "Continuous camera tests require the ffmpeg executable"
    process = await asyncio.create_subprocess_exec(
        binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=15",
        "-t",
        "8",
        "-an",
        "-c:v",
        "libvpx",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        "-threads",
        "1",
        "-g",
        "15",
        "-f",
        "webm",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    video, errors = await process.communicate()
    assert process.returncode == 0, errors.decode()
    assert video.startswith(b"\x1aE\xdf\xa3")
    return video


@pytest.fixture
async def video_robot(tmp_path, native_video):
    peer = await SyntheticVideoRobot(tmp_path / "invented-video-robot", native_video).start()
    try:
        yield peer
    finally:
        await peer.close()


@pytest.fixture
async def video_setup(hass, video_robot):
    entry = await linked_entry(hass, video_robot)
    client = await enable_controls(hass, entry, video_robot, ["camera"])
    camera = entity(hass, entry, "camera", "camera")
    server = TestServer(hass.http.app)
    await server.start_server()
    async with ClientSession() as viewer:
        url = server.make_url(f"/api/camera_proxy_stream/{camera.entity_id}?token={camera.access_tokens[-1]}")
        try:
            yield entry, client, camera, viewer, url
        finally:
            await camera._async_stop_video()
            await server.close()


async def frames(response, count=4):
    multipart = MultipartReader.from_response(response)
    images = []
    async with asyncio.timeout(8):
        for _ in range(count):
            part = await multipart.next()
            assert part is not None and part.headers["Content-Type"] == "image/jpeg"
            image = await part.read()
            assert image.startswith(b"\xff\xd8\xff")
            images.append(bytes(image))
    assert len(set(images)) > 1  # Moving video, not repeated copies of one photo.
    return images


async def test_camera_http_consumes_continuous_native_video_without_auto_activation(
    hass,
    video_robot,
    video_setup,
    monkeypatch,
):
    entry, client, camera, viewer, url = video_setup
    response = await viewer.get(url)
    assert response.status == 503 and video_robot.video_requests == 0
    await response.release()
    commands = []
    real_spawn = asyncio.create_subprocess_exec

    async def recorded_spawn(*argv, **kwargs):
        commands.append(argv)
        return await real_spawn(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorded_spawn)
    await camera.async_turn_on()
    response = await viewer.get(url)
    assert response.status == 200 and response.content_type == "multipart/x-mixed-replace"
    await frames(response, 6)
    assert video_robot.video_requests == 1 and video_robot.video_authorized == [True]
    assert video_robot.snapshot_calls == 0
    assert len(camera._video_sessions) == 1
    decoders = [argv for argv in commands if "pipe:0" in argv]
    assert len(decoders) == 1
    assert "pipe:1" in decoders[0]
    assert all(entry.data["credential"] not in str(argv) and video_robot.host not in str(argv) for argv in decoders)
    session = next(iter(camera._video_sessions))
    process = session.process
    assert process is not None and process.returncode is None
    response.close()
    await wait_for(lambda: process.returncode is not None and not camera._video_sessions)
    assert client.observed_control("camera_active") is True
    assert [call["action"] for call in video_robot.control_calls] == ["start_camera"]


async def test_continuous_video_viewers_are_bounded_and_entity_unload_collects_decoders(hass, video_robot, video_setup):
    entry, _client, camera, viewer, url = video_setup
    await camera.async_turn_on()
    responses = [await viewer.get(url) for _ in range(MAX_VIDEO_VIEWERS)]
    assert all(response.status == 200 for response in responses)
    for response in responses:
        await frames(response)
    rejected = await viewer.get(url)
    assert rejected.status == 429
    assert video_robot.video_requests == MAX_VIDEO_VIEWERS
    processes = [session.process for session in camera._video_sessions]
    assert len(processes) == MAX_VIDEO_VIEWERS and all(process.returncode is None for process in processes)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await wait_for(
        lambda: (
            all(process.returncode is not None for process in processes) and video_robot.active_video_connections == 0
        )
    )
    assert not camera._video_sessions
    assert not camera._video_close_tasks
    for response in responses:
        response.close()
    rejected.close()


async def test_camera_http_authentication_is_required_before_native_video_access(hass, video_robot, video_setup):
    _entry, _client, camera, viewer, url = video_setup
    await camera.async_turn_on()
    unauthorized = await viewer.get(url.with_query({"token": "invented-invalid-camera-token"}))
    assert unauthorized.status == 403
    assert video_robot.video_requests == 0 and not camera._video_sessions
    unauthorized.close()


async def test_native_camera_lease_expiry_collects_the_continuous_decoder(hass, video_robot, video_setup):
    _entry, client, camera, viewer, url = video_setup
    await client.async_control(video_robot.robot_id, "start_camera", {"duration_ms": 1500})
    response = await viewer.get(url)
    assert response.status == 200
    await frames(response)
    process = next(iter(camera._video_sessions)).process
    await wait_for(lambda: client.observed_control("camera_active") is None and process.returncode is not None)
    await wait_for(lambda: not camera._video_sessions and video_robot.active_video_connections == 0)
    assert video_robot.video_requests == 1
    assert [call["action"] for call in video_robot.control_calls] == ["start_camera"]
    response.close()


async def test_decoder_lifetime_ends_the_response_without_reopening_native_video(
    hass, video_robot, video_setup, monkeypatch
):
    _entry, client, camera, viewer, url = video_setup
    monkeypatch.setattr("custom_components.phoenix.camera_stream.MAX_VIDEO_SECONDS", 1.5)
    await camera.async_turn_on()
    response = await viewer.get(url)
    assert response.status == 200
    await frames(response)
    process = next(iter(camera._video_sessions)).process
    async with asyncio.timeout(8):
        await response.read()
    await wait_for(lambda: process.returncode is not None and not camera._video_sessions)
    await wait_for(lambda: video_robot.active_video_connections == 0)
    assert client.observed_control("camera_active") is True
    assert video_robot.video_requests == 1
    assert [call["action"] for call in video_robot.control_calls] == ["start_camera"]
    response.close()


async def test_home_assistant_stop_collects_video_without_starting_a_new_camera_session(hass, video_robot, video_setup):
    _entry, _client, camera, viewer, url = video_setup
    await camera.async_turn_on()
    response = await viewer.get(url)
    assert response.status == 200
    await frames(response)
    process = next(iter(camera._video_sessions)).process
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await wait_for(lambda: process.returncode is not None and not camera._video_sessions)
    assert camera._video_stopping and video_robot.video_requests == 1
    response.close()


@pytest.mark.parametrize("stop_reason", ["native_stop", "privacy_hatch", "permission_removed", "disconnect"])
async def test_native_privacy_and_connection_changes_end_video_without_reopening(
    hass, video_robot, video_setup, stop_reason
):
    _entry, client, camera, viewer, url = video_setup
    await camera.async_turn_on()
    response = await viewer.get(url)
    assert response.status == 200
    await frames(response)
    process = next(iter(camera._video_sessions)).process
    if stop_reason == "native_stop":
        await camera.async_turn_off()
    elif stop_reason == "privacy_hatch":
        video_robot.hatch_closed = False
        video_robot.values["camera_active"] = False
        await video_robot.controls_state()
    elif stop_reason == "permission_removed":
        video_robot.enabled.remove("camera")
        await video_robot.roster()
    else:
        await video_robot.disconnect()
    await wait_for(lambda: process.returncode is not None and not camera._video_sessions)
    assert video_robot.video_requests == 1 and video_robot.snapshot_calls == 0
    assert not camera._video_close_tasks
    response.close()
    if stop_reason == "disconnect":
        assert not client.ready


@pytest.mark.parametrize("fault", ["denied", "wrong_content_type", "bad_ebml", "redirect"])
async def test_native_video_rejects_inactive_malformed_and_redirected_streams(hass, video_robot, video_setup, fault):
    _entry, _client, camera, viewer, url = video_setup
    await camera.async_turn_on()
    if fault == "denied":
        video_robot.video_status = 403
    elif fault == "wrong_content_type":
        video_robot.video_content_type = "application/octet-stream"
    elif fault == "bad_ebml":
        video_robot.video = b"Invented invalid WebM container"
    else:
        video_robot.video_redirect = "https://127.0.0.1:1/invented-leak"
    response = await viewer.get(url)
    assert response.status != 200
    await wait_for(lambda: not camera._video_sessions)
    assert video_robot.video_requests == 1
    assert video_robot.snapshot_calls == 0
    response.close()
