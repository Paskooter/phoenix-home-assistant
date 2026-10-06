"""Decode an authenticated native WebM feed without giving ffmpeg a URL or key."""

import asyncio
from contextlib import suppress

from aiohttp import ClientError, web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .controls_api import async_camera_video

MAX_VIDEO_VIEWERS = 2
MAX_VIDEO_SECONDS = 60
MAX_VIDEO_INPUT_BYTES = 64 * 1024 * 1024
VIDEO_CHUNK_BYTES = 64 * 1024
MJPEG_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=phoenix"


class CameraVideoSession:
    """One bounded viewer/decoder, owned by the config entry and never restarted."""

    def __init__(self, client, robot_id: str) -> None:
        self.client = client
        self.robot_id = robot_id
        self.stop_requested = False
        self.process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._pump_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._upstream = None

    def _allowed(self) -> bool:
        return (
            not self.stop_requested
            and self.client.control_available(self.robot_id, "camera_streaming")
            and self.client.observed_control("camera_active") is True
        )

    def _inactive(self) -> None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="control_camera_inactive")

    async def async_stream(self, request: web.Request) -> web.StreamResponse:
        """Stream decoded video through HA's authenticated camera endpoint."""
        if not self._allowed():
            self._inactive()
        self._task = self.client.entry.async_create_background_task(
            self.client.hass, self._run(request), "Jibo continuous camera video"
        )
        try:
            return await self._task
        finally:
            await self.async_close()

    async def _spawn_decoder(self) -> None:
        # Only an in-memory pipe is available to the decoder. Installation
        # credentials and robot addresses never appear in its argv or logs.
        binary = get_ffmpeg_manager(self.client.hass).binary
        spawned = asyncio.create_task(
            asyncio.create_subprocess_exec(
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostats",
                "-protocol_whitelist",
                "pipe",
                "-threads",
                "1",
                "-probesize",
                "1048576",
                "-analyzeduration",
                "100000",
                "-f",
                "webm",
                "-i",
                "pipe:0",
                "-an",
                "-filter_threads",
                "1",
                "-vf",
                "fps=15",
                "-c:v",
                "mjpeg",
                "-threads",
                "1",
                "-q:v",
                "5",
                "-f",
                "mpjpeg",
                "-boundary_tag",
                "phoenix",
                "-flush_packets",
                "1",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=VIDEO_CHUNK_BYTES,
            )
        )
        try:
            self.process = await asyncio.shield(spawned)
        except asyncio.CancelledError:
            # A cancellation during process creation must still collect and
            # terminate a child which the operating system already started.
            while not spawned.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(spawned)
            self.process = spawned.result()
            raise

    async def _pump_input(self, upstream, prefix: bytes) -> None:
        process = self.process
        assert process is not None and process.stdin is not None
        size = len(prefix)
        try:
            process.stdin.write(prefix)
            await process.stdin.drain()
            async for chunk in upstream.content.iter_chunked(VIDEO_CHUNK_BYTES):
                if not self._allowed():
                    break
                size += len(chunk)
                if size > MAX_VIDEO_INPUT_BYTES:
                    break
                process.stdin.write(chunk)
                await process.stdin.drain()
        except ClientError, ConnectionError, BrokenPipeError, TimeoutError:
            pass  # An ended stream is never reopened or replayed.
        finally:
            process.stdin.close()
            upstream.close()

    async def _run(self, request: web.Request) -> web.StreamResponse:
        response = None
        try:
            async with asyncio.timeout(MAX_VIDEO_SECONDS):
                async with async_camera_video(self.client, self.robot_id) as (upstream, prefix):
                    self._upstream = upstream
                    if not self._allowed():
                        self._inactive()
                    await self._spawn_decoder()
                    if not self._allowed():
                        self._inactive()
                    self._pump_task = asyncio.create_task(self._pump_input(upstream, prefix))
                    assert self.process is not None and self.process.stdout is not None
                    async with asyncio.timeout(10):
                        first = await self.process.stdout.read(VIDEO_CHUNK_BYTES)
                    if not first or not self._allowed():
                        self._inactive()
                    response = web.StreamResponse(
                        headers={
                            "Content-Type": MJPEG_CONTENT_TYPE,
                            "Cache-Control": "no-store",
                            "X-Content-Type-Options": "nosniff",
                        }
                    )
                    await response.prepare(request)
                    await response.write(first)
                    while self._allowed():
                        chunk = await self.process.stdout.read(VIDEO_CHUNK_BYTES)
                        if not chunk or not self._allowed():
                            break
                        await response.write(chunk)
                    with suppress(ConnectionError, RuntimeError):
                        await response.write_eof()
                    return response
        except TimeoutError:
            if response is None:
                self._inactive()
            with suppress(ConnectionError, RuntimeError):
                await response.write_eof()
            return response
        finally:
            await self._cleanup_safely()

    async def _cleanup_safely(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup())
        while not self._cleanup_task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._cleanup_task)
        self._cleanup_task.result()

    async def _cleanup(self) -> None:
        self.stop_requested = True
        if self._upstream is not None:
            self._upstream.close()
            self._upstream = None
        if self._pump_task is not None:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
            self._pump_task = None
        if self.process is not None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            # A subprocess wait can remain blocked when an unread stdout pipe
            # is full. Drain discarded output during termination, never into
            # the viewer, so unload cannot strand a decoder.
            draining = asyncio.create_task(self._discard_output())
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
            try:
                async with asyncio.timeout(2):
                    await self.process.wait()
                    await asyncio.shield(draining)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
            draining.cancel()
            await asyncio.gather(draining, return_exceptions=True)

    async def _discard_output(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        while await self.process.stdout.read(VIDEO_CHUNK_BYTES):
            pass

    async def async_close(self) -> None:
        """Release HTTP/decoder work before entity or integration unload completes."""
        self.stop_requested = True
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        else:
            await self._cleanup_safely()
