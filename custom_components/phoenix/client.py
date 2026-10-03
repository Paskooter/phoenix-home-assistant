"""Outbound bridge with at-most-once execution and no reconnect replay."""

import asyncio
import logging
import random
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from uuid import UUID

from aiohttp import (
    ClientError,
    ClientHandlerType,
    ClientRequest,
    ClientResponse,
    ClientSession,
    ClientWebSocketResponse,
    ClientWSTimeout,
    WSMsgType,
    WSServerHandshakeError,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import CONF_CONVERSATION_AGENT, MAX_COMMANDS, MAX_FRAME_BYTES, MAX_SEEN_REQUESTS, PROTOCOL_VERSION, VERSION

_LOGGER = logging.getLogger(__name__)


async def reject_redirects(request: ClientRequest, handler: ClientHandlerType) -> ClientResponse:
    """Keep the authenticated WebSocket on the configured TLS origin."""
    response = await handler(request)
    if response.status in (301, 302, 303, 307, 308):
        response.close()
        raise ClientError("Phoenix connector redirects are not allowed")
    return response


def error_result(code: str, outcome: str = "error") -> dict[str, Any]:
    """A protocol error never claims a device changed state."""
    return {"outcome": outcome, "response_type": "error", "speech": "", "code": code}


def conversation_result(result: dict[str, Any], hass: HomeAssistant | None = None) -> dict[str, Any]:
    """Preserve partial/uncertain results and use only plain returned speech."""
    response = result.get("response", {})
    response_type = response.get("response_type", "error")
    data = response.get("data") or {}
    success = data.get("success") or []
    failed = data.get("failed") or []
    code = data.get("code", "unknown")
    speech = response.get("speech", {}).get("plain", {}).get("speech", "")
    # HA's service handler can return success for a skipped unavailable entity.
    # Inspect only targets already resolved by HA; never parse names ourselves.
    unavailable = []
    if hass is not None and response_type == "action_done":
        entities = [target for target in success if target.get("type") == "entity"]
        unavailable = [
            target
            for target in entities
            if (state := hass.states.get(target.get("id", ""))) is None
            or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ]
        if unavailable:
            success = [target for target in entities if target not in unavailable]
            failed = [*failed, *unavailable]
            speech = "Some devices were unavailable." if success else "The device is unavailable."
    if response_type not in ("action_done", "query_answer", "error"):
        return error_result("invalid_result", "uncertain")
    if response_type == "error":
        outcome = "error" if code in ("no_intent_match", "no_valid_targets") else "uncertain"
    elif failed:
        outcome = "partial" if success else ("uncertain" if unavailable else "error")
    else:
        outcome = "success"
    normalized = {
        "outcome": outcome,
        "response_type": response_type,
        "speech": speech[:500] if isinstance(speech, str) else "",
        "success_count": len(success),
        "failed_count": len(failed),
    }
    if response_type == "error":
        normalized["code"] = code
    if result.get("conversation_id"):
        normalized["conversation_id"] = str(result["conversation_id"])[:100]
    return normalized


class PhoenixClient:
    """One installation's connector and bounded command tasks."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: ClientSession,
        *,
        auth_failed: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.session = session
        self.auth_failed = auth_failed
        self.state = "disconnected"
        self.last_error: str | None = None
        self.listeners: set[Callable[[], None]] = set()
        self.socket: ClientWebSocketResponse | None = None
        self.session_id: str | None = None
        self.session_server_time = 0
        self.session_local_time = 0.0
        self.commands: dict[str, asyncio.Task] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.seen: dict[str, int] = {}
        self.storage: Store[dict[str, int]] = Store(hass, 1, f"phoenix.{entry.data['installation_id']}.requests")
        self.stopping = False
        self.ready = False
        self.last_connected: float | None = None

    @callback
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Listen to connection health changes without polling."""
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    @callback
    def set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.last_error = error
        for listener in tuple(self.listeners):
            listener()

    async def async_run(self) -> None:
        """Reconnect transport only. Commands are never queued for reconnect."""
        try:
            self.seen = await self.storage.async_load() or {}
            if not isinstance(self.seen, dict) or any(
                not isinstance(key, str) or not isinstance(expiry, int) for key, expiry in self.seen.items()
            ):
                raise ValueError("Invalid request storage")
        except Exception:  # A corrupt dedupe store must fail closed.
            self.set_state("protocol_error", "request_storage")
            _LOGGER.error("Phoenix request storage unavailable; repair or relink the integration")
            return
        attempts = 0
        while not self.stopping:
            self.set_state("connecting")
            try:
                async with asyncio.timeout(10):
                    socket = await self.session.ws_connect(
                        self.entry.data["phoenix_url"].replace("https://", "wss://", 1) + "/api/home-assistant/connect",
                        headers={"Authorization": f"Bearer {self.entry.data['credential']}"},
                        max_msg_size=MAX_FRAME_BYTES,
                        timeout=ClientWSTimeout(ws_close=10),
                        autoping=True,
                        autoclose=True,
                    )
                async with socket:
                    self.socket = socket
                    self.ready = False
                    self.results.clear()
                    async with asyncio.timeout(10):
                        message = await socket.receive_json()
                    if not self._welcome(message):
                        self.set_state("protocol_error", "unsupported_protocol")
                        return
                    await self._send(
                        {
                            "type": "ready",
                            "agent": "home_assistant",
                            "ha_version": HA_VERSION,
                            "integration_version": VERSION,
                        }
                    )
                    self.ready = True
                    self.last_connected = time.time()
                    self.set_state("connected")
                    attempts = 0
                    async for message in socket:
                        if message.type == WSMsgType.TEXT:
                            await self._receive(message.json())
                        elif message.type == WSMsgType.ERROR:
                            break
                    if socket.close_code == 4001:
                        self.set_state("authentication_required", "invalid_auth")
                        self.auth_failed()
                        return
                    if socket.close_code == 4003:
                        self.set_state("protocol_error", "unsupported_protocol")
                        return
                    if socket.close_code == 4002:
                        self.set_state("protocol_error", "connection_replaced")
                        return
            except WSServerHandshakeError as err:
                if err.status in (401, 403):
                    self.set_state("authentication_required", "invalid_auth")
                    self.auth_failed()
                    return
                self.set_state("disconnected", "cannot_connect")
            except ClientError, ConnectionError, TimeoutError, ValueError, TypeError, KeyError:
                self.set_state("disconnected", "cannot_connect")
            finally:
                self.ready = False
                self.socket = None
                await self._cancel_commands()
            if not self.stopping:
                self.set_state("disconnected", "cannot_connect")
                attempts += 1
                await asyncio.sleep(min(60, 2 ** min(attempts, 6)) + random.uniform(0, 1))

    def _welcome(self, message: dict[str, Any]) -> bool:
        if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION or message.get("type") != "welcome":
            return False
        try:
            self.session_id = str(UUID(message["session_id"]))
            self.session_server_time = int(message["server_time_ms"])
            self.session_local_time = asyncio.get_running_loop().time()
            return True
        except KeyError, ValueError, TypeError:
            return False

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.socket is None or self.socket.closed:
            raise ConnectionError("Disconnected")
        await self.socket.send_json({"v": PROTOCOL_VERSION, "session_id": self.session_id, **payload})

    def _remaining(self, deadline: int) -> float:
        elapsed = asyncio.get_running_loop().time() - self.session_local_time
        return (deadline - self.session_server_time) / 1000 - elapsed

    async def _receive(self, frame: dict[str, Any]) -> None:
        if (
            not isinstance(frame, dict)
            or frame.get("v") != PROTOCOL_VERSION
            or frame.get("session_id") != self.session_id
        ):
            raise ValueError("Invalid session")
        if frame.get("type") != "command":
            raise ValueError("Unsupported frame")
        request_id = str(UUID(frame["request_id"]))
        str(UUID(frame["robot_id"]))
        text = frame.get("text")
        deadline = frame.get("deadline_ms")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 500
            or not isinstance(deadline, int)
            or isinstance(deadline, bool)
            or frame.get("language") != "en"
        ):
            raise ValueError("Invalid command")
        remaining = self._remaining(deadline)
        if remaining <= 0:
            await self._send({"type": "result", "request_id": request_id, "result": error_result("expired", "expired")})
            return
        if remaining > 15:
            raise ValueError("Invalid deadline")
        if request_id in self.commands:
            await self._send({"type": "accepted", "request_id": request_id})
            return
        if request_id in self.results:
            await self._send({"type": "result", "request_id": request_id, "result": self.results[request_id]})
            return
        if request_id in self.seen:
            await self._send(
                {
                    "type": "result",
                    "request_id": request_id,
                    "result": error_result("duplicate_not_replayed", "uncertain"),
                }
            )
            return
        now_ms = self.session_server_time + int((asyncio.get_running_loop().time() - self.session_local_time) * 1000)
        self.seen = {key: expiry for key, expiry in self.seen.items() if expiry > now_ms}
        self.results = {key: value for key, value in self.results.items() if key in self.seen}
        if len(self.commands) >= MAX_COMMANDS or len(self.seen) >= MAX_SEEN_REQUESTS:
            await self._send({"type": "result", "request_id": request_id, "result": error_result("busy")})
            return
        # Persist a tombstone BEFORE acknowledging or executing. On restart,
        # duplicate work gets uncertainty, never an action retry.
        self.seen[request_id] = deadline + 60_000
        try:
            await self.storage.async_save(self.seen)
        except Exception:
            await self._send({"type": "result", "request_id": request_id, "result": error_result("request_storage")})
            return
        if self._remaining(deadline) <= 0:
            await self._send({"type": "result", "request_id": request_id, "result": error_result("expired", "expired")})
            return
        await self._send({"type": "accepted", "request_id": request_id})
        task = self.entry.async_create_background_task(
            self.hass, self._execute(request_id, text, deadline), "Phoenix Assist command"
        )
        self.commands[request_id] = task
        task.add_done_callback(lambda _task: self.commands.pop(request_id, None))

    async def _execute(self, request_id: str, text: str, deadline: int) -> None:
        started = False
        try:
            remaining = self._remaining(deadline)
            if remaining <= 0:
                result = error_result("expired", "expired")
            elif (
                conversation.async_get_agent(
                    self.hass, self.entry.options.get(CONF_CONVERSATION_AGENT, conversation.HOME_ASSISTANT_AGENT)
                )
                is None
            ):
                # A removed agent must never silently switch to another one.
                result = {
                    **error_result("agent_unavailable"),
                    "speech": "The selected Assist agent is unavailable. Check Phoenix settings in Home Assistant.",
                }
            else:
                started = True
                async with asyncio.timeout(remaining):
                    answer = await conversation.async_converse(
                        self.hass,
                        text=text,
                        conversation_id=None,
                        context=Context(),
                        language="en",
                        agent_id=self.entry.options.get(CONF_CONVERSATION_AGENT, conversation.HOME_ASSISTANT_AGENT),
                    )
                    result = conversation_result(answer.as_dict(), self.hass)
                    await self._confirm_on_off(answer)
        except asyncio.CancelledError:
            raise  # The broker reports uncertainty after socket loss/unload.
        except Exception:
            result = error_result("confirmation_lost", "uncertain" if started else "error")
        self.results[request_id] = result
        while len(self.results) > MAX_SEEN_REQUESTS:
            self.results.pop(next(iter(self.results)))
        # Only this live connection may receive this result. Clear on reconnect.
        with suppress(ClientError, ConnectionError, RuntimeError):
            await self._send({"type": "result", "request_id": request_id, "result": result})

    async def _confirm_on_off(self, answer: conversation.ConversationResult) -> None:
        """HA may return while a device service is still running in background.

        Wait for HA's resolved light/switch targets to reflect the requested
        state within the existing deadline. Never issue a second service call.
        Scripts, scenes and custom intents retain HA's own completion semantics.
        """
        response = answer.response
        intent = response.intent
        if intent is None or intent.intent_type not in ("HassTurnOn", "HassTurnOff"):
            return
        expected = "on" if intent.intent_type == "HassTurnOn" else "off"
        targets = [
            target.id
            for target in response.success_results
            if target.type == "entity"
            and target.id
            and target.id.startswith(("light.", "switch."))
            and (state := self.hass.states.get(target.id)) is not None
            and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ]
        while targets:
            if all(
                (state := self.hass.states.get(entity_id)) is not None and state.state == expected
                for entity_id in targets
            ):
                return
            await asyncio.sleep(0.05)

    async def _cancel_commands(self) -> None:
        tasks = tuple(self.commands.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.commands.clear()

    async def async_stop(self) -> None:
        self.stopping = True
        if self.socket is not None:
            await self.socket.close()
        await self._cancel_commands()
        self.set_state("disconnected")
