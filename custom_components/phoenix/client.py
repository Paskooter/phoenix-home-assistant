"""Outbound bridge with at-most-once execution and no reconnect replay."""

import asyncio
import logging
import math
import random
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import time as local_time
from typing import Any
from uuid import UUID, uuid4

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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import color as color_util
from homeassistant.util import dt as dt_util

from .commands import (
    apply_follow_up,
    apply_relative_brightness,
    device_area,
    exposed,
    implicit_room_command,
    normalize_phrase,
    pronoun_action,
    pronoun_light_settings,
    query_subject,
    read_query,
    read_room_follow_up,
    relative_brightness_step,
    resolved_targets,
    state_domain,
    validate_shortcuts,
)
from .const import (
    ANNOUNCEMENT_SECONDS,
    CAPABILITIES,
    CONF_CONVERSATION_AGENT,
    CONF_QUIET_HOURS_ENABLED,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_ROUTINE_SHORTCUTS,
    DEFAULT_QUIET_HOURS_END,
    DEFAULT_QUIET_HOURS_START,
    DOMAIN,
    FOLLOW_UP_SECONDS,
    MAX_ANNOUNCEMENT_CHARS,
    MAX_COMMANDS,
    MAX_FRAME_BYTES,
    MAX_ROBOTS,
    MAX_SEEN_REQUESTS,
    PROTOCOL_VERSION,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class RobotState:
    """Private local device identity and bounded health, never diagnostic exports."""

    robot_id: str
    name: str
    device_id: str
    online: bool = False
    busy: bool = False
    announcements_allowed: bool = False
    announcements_supported: bool = False
    last_response: str | None = None
    last_outcome: str | None = None
    last_error: str | None = None
    latency_ms: float | None = None
    selected_agent: str | None = None
    commands_completed: int = 0


@dataclass
class RobotContext:
    """A short per-robot context with resolved IDs, without stored utterances."""

    conversation_id: str
    agent_id: str
    expires_at: float
    expires_at_ms: int
    targets: tuple[str, ...] = ()
    last_action: str | None = None
    read_only: bool = False
    query_subject: str | None = None


@dataclass
class PendingAction:
    """One live, never replayed reverse request."""

    robot_id: str
    deadline_ms: int
    future: asyncio.Future


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
        self.capabilities: set[str] = set()
        self.robots: dict[str, RobotState] = {}
        self.contexts: dict[str, RobotContext] = {}
        self.pending_actions: dict[str, PendingAction] = {}
        self._robot_commands: set[str] = set()
        self._context_timers: dict[str, Callable[[], None]] = {}
        self._roster_listeners: set[Callable[[], None]] = set()
        try:
            self.shortcuts = validate_shortcuts(entry.options.get(CONF_ROUTINE_SHORTCUTS, []))
        except ValueError:
            self.shortcuts = []  # Invalid local options never offer execution shortcuts.

    @property
    def agent_id(self) -> str:
        return self.entry.options.get(CONF_CONVERSATION_AGENT, conversation.HOME_ASSISTANT_AGENT)

    @callback
    def subscribe_roster(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Let platforms add entities when authenticated robots arrive."""
        self._roster_listeners.add(listener)
        return lambda: self._roster_listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self.listeners):
            listener()

    @callback
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Listen to connection health changes without polling."""
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    @callback
    def set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.last_error = error
        self._notify()

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
                            "capabilities": list(CAPABILITIES),
                        }
                    )
                    self.ready = True
                    self.last_connected = time.time()
                    self.set_state("connected")
                    await self._send_preferences()
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
                self._clear_contexts()
                self._fail_actions()
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
            capabilities = message.get("capabilities", [])
            if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
                return False
            self.capabilities = set(capabilities).intersection(CAPABILITIES)
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

    def _server_now_ms(self) -> int:
        return self.session_server_time + int((asyncio.get_running_loop().time() - self.session_local_time) * 1000)

    async def _send_preferences(self) -> None:
        if not self.ready or not self.capabilities.intersection(("follow_up", "routine_shortcuts")):
            return
        now = asyncio.get_running_loop().time()
        shortcuts = (
            [
                {"id": item["id"], "phrase": item["phrase"]}
                for item in self.shortcuts
                if self.hass.states.get(item["entity_id"]) is not None and exposed(self.hass, item["entity_id"])
            ]
            if "routine_shortcuts" in self.capabilities
            else []
        )
        await self._send(
            {
                "type": "preferences",
                "shortcuts": shortcuts,
                "follow_up": [
                    {"robot_id": robot_id, "available": True, "expires_at_ms": context.expires_at_ms}
                    for robot_id, context in self.contexts.items()
                    if "follow_up" in self.capabilities
                    and robot_id in self.robots
                    and context.expires_at > now
                    and context.agent_id == self.agent_id
                ],
            }
        )

    async def async_refresh_preferences(self) -> None:
        """Refresh exposure-driven preferences only on the live connection."""
        with suppress(ClientError, ConnectionError, RuntimeError):
            await self._send_preferences()

    def _context(self, robot_id: str | None) -> RobotContext | None:
        if not robot_id or not (context := self.contexts.get(robot_id)):
            return None
        if context.agent_id != self.agent_id or context.expires_at <= asyncio.get_running_loop().time():
            self.contexts.pop(robot_id, None)
            if cancel := self._context_timers.pop(robot_id, None):
                cancel()
            return None
        return context

    def _remember(
        self,
        robot_id: str | None,
        conversation_id: str,
        targets: tuple[str, ...],
        action: str | None = None,
        read_only: bool = False,
        subject: str | None = None,
    ) -> None:
        if not robot_id or "follow_up" not in self.capabilities or robot_id not in self.robots:
            return
        if (
            not isinstance(conversation_id, str)
            or not 1 <= len(conversation_id) <= 100
            or len(targets) > 32
            or any(entity_id.startswith(("scene.", "script.")) for entity_id in targets)
        ):
            return  # Routine IDs must never become a pronoun device target.
        if cancel := self._context_timers.pop(robot_id, None):
            cancel()
        context = RobotContext(
            conversation_id=conversation_id,
            agent_id=self.agent_id,
            expires_at=asyncio.get_running_loop().time() + FOLLOW_UP_SECONDS,
            expires_at_ms=self._server_now_ms() + FOLLOW_UP_SECONDS * 1000,
            targets=targets,
            last_action=action,
            read_only=read_only,
            query_subject=subject,
        )
        self.contexts[robot_id] = context

        @callback
        def expire(_now) -> None:
            if self.contexts.get(robot_id) is context:
                self.contexts.pop(robot_id, None)
                self._context_timers.pop(robot_id, None)
                self._notify()

        self._context_timers[robot_id] = async_call_later(self.hass, FOLLOW_UP_SECONDS, expire)

    def _clear_contexts(self) -> None:
        for cancel in self._context_timers.values():
            cancel()
        self._context_timers.clear()
        self.contexts.clear()

    def _fail_actions(self) -> None:
        for action in self.pending_actions.values():
            if not action.future.done():
                action.future.set_result(error_result("confirmation_lost", "uncertain"))

    def _roster(self, frame: dict[str, Any]) -> None:
        if "robot_roster" not in self.capabilities:
            raise ValueError("Unnegotiated roster")
        robots = frame.get("robots")
        if not isinstance(robots, list) or len(robots) > MAX_ROBOTS:
            raise ValueError("Invalid roster")
        registry = dr.async_get(self.hass)
        next_robots = {}
        for value in robots:
            if not isinstance(value, dict):
                raise ValueError("Invalid robot")
            robot_id = str(UUID(value["robot_id"]))
            name = value.get("name")
            if (
                robot_id in next_robots
                or not isinstance(name, str)
                or not 1 <= len(name) <= 100
                or any(ord(char) < 32 for char in name)
                or any(not isinstance(value.get(key), bool) for key in ("online", "busy", "announcements_allowed"))
            ):
                raise ValueError("Invalid robot")
            device = registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers={(DOMAIN, f"{self.entry.data['installation_id']}/{robot_id}")},
                manufacturer="Jibo",
                model="Jibo",
                name=name,
                configuration_url=f"{self.entry.data['phoenix_url']}/app#/home-assistant",
            )
            robot = self.robots.get(robot_id) or RobotState(robot_id, name, device.id)
            robot.name, robot.device_id = name, device.id
            robot.online = value["online"]
            robot.busy = value["busy"]
            robot.announcements_allowed = value["announcements_allowed"]
            robot.announcements_supported = value.get("announcements_supported") is True
            next_robots[robot_id] = robot
        for robot_id in set(self.contexts).difference(next_robots):
            self.contexts.pop(robot_id, None)
            if cancel := self._context_timers.pop(robot_id, None):
                cancel()
        self.robots = next_robots
        for listener in tuple(self._roster_listeners):
            listener()
        self._notify()

    async def _receive(self, frame: dict[str, Any]) -> None:
        if (
            not isinstance(frame, dict)
            or frame.get("v") != PROTOCOL_VERSION
            or frame.get("session_id") != self.session_id
        ):
            raise ValueError("Invalid session")
        if frame.get("type") == "roster":
            self._roster(frame)
            await self._send_preferences()
            return
        if frame.get("type") == "action_result":
            self._action_result(frame)
            return
        if frame.get("type") != "command":
            raise ValueError("Unsupported frame")
        request_id = str(UUID(frame["request_id"]))
        robot_id = str(UUID(frame["robot_id"]))
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
        route = frame.get("route", {"kind": "command"})
        if not isinstance(route, dict) or route.get("kind") not in ("command", "query", "follow_up", "routine"):
            raise ValueError("Invalid route")
        kind = route["kind"]
        required = {"query": "state_queries", "follow_up": "follow_up", "routine": "routine_shortcuts"}.get(kind)
        if required and required not in self.capabilities:
            raise ValueError("Unnegotiated route")
        if kind == "routine":
            route = {"kind": kind, "shortcut_id": str(UUID(route["shortcut_id"]))}
        elif "shortcut_id" in route:
            raise ValueError("Invalid route")
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
        if "robot_roster" in self.capabilities and robot_id not in self.robots:
            await self._send({"type": "result", "request_id": request_id, "result": error_result("robot_unavailable")})
            return
        if (
            len(self.commands) + len(self.pending_actions) >= MAX_COMMANDS
            or len(self.seen) >= MAX_SEEN_REQUESTS
            or robot_id in self._robot_commands
            or any(action.robot_id == robot_id for action in self.pending_actions.values())
        ):
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
            self.hass, self._execute(request_id, text, deadline, robot_id, route), "Phoenix Assist command"
        )
        self.commands[request_id] = task
        self._robot_commands.add(robot_id)

        def complete(_task):
            self.commands.pop(request_id, None)
            self._robot_commands.discard(robot_id)

        task.add_done_callback(complete)

    async def _execute(
        self,
        request_id: str,
        text: str,
        deadline: int,
        robot_id: str | None = None,
        route: dict[str, Any] | None = None,
    ) -> None:
        started = False
        start_time = asyncio.get_running_loop().time()
        kind = (route or {}).get("kind", "command")
        robot = self.robots.get(robot_id)
        device_id = robot.device_id if robot and "room_context" in self.capabilities else None
        remembered = self._context(robot_id) if kind == "follow_up" else None
        # A failed new target or a routine must not leave an older light as the
        # implicit target of the next turn. Only a confirmed result restores it.
        if robot_id:
            self.contexts.pop(robot_id, None)
            if cancel := self._context_timers.pop(robot_id, None):
                cancel()
        context = Context()
        conversation_id = remembered.conversation_id if remembered else context.id
        targets: tuple[str, ...] = ()
        last_action = None
        read_only = False
        subject = None
        try:
            remaining = self._remaining(deadline)
            if remaining <= 0:
                result = error_result("expired", "expired")
            elif (
                kind == "command"
                and robot
                and "room_context" in self.capabilities
                and implicit_room_command(text)
                and device_area(self.hass, device_id) is None
            ):
                result = {
                    **error_result("room_not_configured"),
                    "speech": "Assign Jibo to a room in Home Assistant before using room commands.",
                }
            elif kind == "follow_up" and remembered is None:
                result = error_result("no_context")
            elif (
                remembered
                and normalize_phrase(text).startswith("and in ")
                and (remembered.read_only or remembered.last_action)
            ):
                result, targets = read_room_follow_up(
                    self.hass, text, device_id, remembered.targets, remembered.query_subject
                )
                if remembered.read_only:
                    read_only = True
                    subject = remembered.query_subject
                elif result["outcome"] == "success":
                    started = True
                    async with asyncio.timeout(remaining):
                        result = await apply_follow_up(self.hass, targets, remembered.last_action, context)
                        if result["outcome"] == "success":
                            await self._confirm_targets(targets, remembered.last_action)
                            last_action = remembered.last_action
            elif kind == "query" or (
                kind == "follow_up" and normalize_phrase(text).startswith(("is ", "are ", "what ", "what's "))
            ):
                # This path has no service calls and never invokes any selected
                # agent, including agents with an executing LLM fallback.
                result, targets = read_query(self.hass, text, device_id, remembered.targets if remembered else ())
                read_only = True
                subject = query_subject(text) or (remembered.query_subject if remembered else None)
            elif kind == "routine":
                shortcut = next((item for item in self.shortcuts if item["id"] == route["shortcut_id"]), None)
                if shortcut is None or normalize_phrase(text) != shortcut["phrase"]:
                    result = error_result("invalid_shortcut")
                elif (state := self.hass.states.get(shortcut["entity_id"])) is None or not exposed(
                    self.hass, shortcut["entity_id"]
                ):
                    result = error_result("no_valid_targets")
                elif state.state == STATE_UNAVAILABLE or (
                    state.state == STATE_UNKNOWN and state_domain(state) != "scene"
                ):
                    result = error_result("target_unavailable")
                else:
                    started = True
                    async with asyncio.timeout(remaining):
                        await self.hass.services.async_call(
                            state_domain(state),
                            "turn_on",
                            {"entity_id": state.entity_id},
                            blocking=True,
                            context=context,
                        )
                    # A started scene/script cannot confirm its downstream effects.
                    result = {
                        "outcome": "success",
                        "response_type": "action_done",
                        "speech": "The routine was started.",
                        "success_count": 1,
                        "failed_count": 0,
                    }
                    targets = (state.entity_id,)
            elif (step := relative_brightness_step(text)) is not None:
                if kind != "follow_up" or remembered is None or not remembered.targets:
                    result = error_result("no_context")
                else:
                    started = True
                    result = await apply_relative_brightness(
                        self.hass, remembered.targets, step, context, lambda: self._remaining(deadline)
                    )
                    if result["outcome"] == "success":
                        targets = remembered.targets
            elif remembered and remembered.targets and (action := pronoun_action(text)):
                started = True
                async with asyncio.timeout(remaining):
                    result = await apply_follow_up(self.hass, remembered.targets, action, context)
                    if result["outcome"] == "success":
                        await self._confirm_targets(remembered.targets, action)
                        targets, last_action = remembered.targets, action
            elif remembered and remembered.targets and (parameters := pronoun_light_settings(text)):
                started = True
                async with asyncio.timeout(remaining):
                    result = await apply_follow_up(self.hass, remembered.targets, "on", context, parameters)
                    if result["outcome"] == "success":
                        expected = "off" if parameters.get("brightness") == 0 else "on"
                        await self._confirm_targets(remembered.targets, expected)
                        await self._confirm_light_settings(remembered.targets, parameters)
                        targets = remembered.targets
            elif conversation.async_get_agent(self.hass, self.agent_id) is None:
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
                        conversation_id=conversation_id if remembered else None,
                        context=context,
                        language="en",
                        agent_id=self.agent_id,
                        device_id=device_id,
                    )
                    result = conversation_result(answer.as_dict(), self.hass)
                    await self._confirm_on_off(answer)
                    conversation_id = answer.conversation_id or conversation_id
                    targets = resolved_targets(answer)
                    if answer.response.intent and answer.response.intent.intent_type in ("HassTurnOn", "HassTurnOff"):
                        last_action = "on" if answer.response.intent.intent_type == "HassTurnOn" else "off"
        except asyncio.CancelledError:
            raise  # The broker reports uncertainty after socket loss/unload.
        except Exception:
            result = error_result("confirmation_lost", "uncertain" if started else "error")
        if result["outcome"] == "success" and kind != "routine":
            self._remember(robot_id, conversation_id, targets, last_action, read_only, subject)
        elif result["outcome"] in ("uncertain", "partial") and robot_id:
            self.contexts.pop(robot_id, None)
        if robot:
            robot.last_response = result["response_type"]
            robot.last_outcome = result["outcome"]
            robot.last_error = result.get("code")
            robot.latency_ms = round((asyncio.get_running_loop().time() - start_time) * 1000, 1)
            robot.selected_agent = self.agent_id
            robot.commands_completed += 1
            self._notify()
        self.results[request_id] = result
        while len(self.results) > MAX_SEEN_REQUESTS:
            self.results.pop(next(iter(self.results)))
        # Only this live connection may receive this result. Clear on reconnect.
        with suppress(ClientError, ConnectionError, RuntimeError):
            await self._send({"type": "result", "request_id": request_id, "result": result})
            await self._send_preferences()

    async def _confirm_targets(self, targets: tuple[str, ...], expected: str) -> None:
        while not all(
            (state := self.hass.states.get(entity_id)) is not None and state.state == expected for entity_id in targets
        ):
            await asyncio.sleep(0.05)

    async def _confirm_light_settings(self, targets: tuple[str, ...], parameters: dict[str, Any]) -> None:
        def finite(value: Any) -> bool:
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

        def matches(entity_id: str) -> bool:
            if (state := self.hass.states.get(entity_id)) is None:
                return False
            if parameters.get("brightness") == 0:
                return state.state == "off"
            for key, expected in parameters.items():
                actual = state.attributes.get(key)
                if key == "rgb_color":
                    # HS lights normalize RGB intensity while keeping their
                    # separate brightness. Confirm hue/saturation instead of
                    # mistaking green (0,128,0) -> (0,255,0) for a lost result.
                    actual_hs = state.attributes.get("hs_color")
                    if not isinstance(actual_hs, (tuple, list)) or len(actual_hs) != 2:
                        if (
                            not isinstance(actual, (tuple, list))
                            or len(actual) != 3
                            or not all(finite(value) and 0 <= value <= 255 for value in actual)
                            or not any(actual)
                        ):
                            return False
                        actual_hs = color_util.color_RGB_to_hs(*actual)
                    if (
                        not all(finite(value) for value in actual_hs)
                        or not 0 <= actual_hs[0] <= 360
                        or not 0 <= actual_hs[1] <= 100
                    ):
                        return False
                    expected_hs = color_util.color_RGB_to_hs(*expected)
                    hue_difference = abs(actual_hs[0] - expected_hs[0]) % 360
                    if abs(actual_hs[1] - expected_hs[1]) > 2 or (
                        expected_hs[1] > 2 and min(hue_difference, 360 - hue_difference) > 2
                    ):
                        return False
                elif (
                    not finite(actual)
                    or (key == "brightness" and not 0 <= actual <= 255)
                    or abs(actual - expected) > (50 if key == "color_temp_kelvin" else 1)
                ):
                    return False
            return True

        while not all(matches(entity_id) for entity_id in targets):
            await asyncio.sleep(0.05)

    def _quiet_hours(self) -> bool:
        if not self.entry.options.get(CONF_QUIET_HOURS_ENABLED, False):
            return False
        try:
            start = local_time.fromisoformat(self.entry.options.get(CONF_QUIET_HOURS_START, DEFAULT_QUIET_HOURS_START))
            end = local_time.fromisoformat(self.entry.options.get(CONF_QUIET_HOURS_END, DEFAULT_QUIET_HOURS_END))
        except ValueError, TypeError:
            return True  # Malformed enabled quiet hours fail closed.
        now = dt_util.as_local(dt_util.utcnow()).time().replace(tzinfo=None)
        if start == end:
            return True
        return start <= now < end if start < end else now >= start or now < end

    def _announcement_error(self, code: str, *, uncertain: bool = False) -> None:
        error = HomeAssistantError if uncertain else ServiceValidationError
        raise error(translation_domain=DOMAIN, translation_key=code)

    async def async_announce(self, robot_id: str, message: str) -> dict[str, Any]:
        """Send once over this live TLS connection and await spoken completion."""
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > MAX_ANNOUNCEMENT_CHARS
            or any(ord(char) < 32 and char not in "\n\t" for char in message)
        ):
            self._announcement_error("invalid_message")
        if self.stopping or not self.ready or self.socket is None or self.socket.closed:
            self._announcement_error("disconnected")
        if "robot_action" not in self.capabilities:
            self._announcement_error("unsupported_announcements")
        robot = self.robots.get(robot_id)
        if robot is None:
            self._announcement_error("permission_denied")
        if not robot.announcements_allowed:
            self._announcement_error("permission_denied")
        if not robot.announcements_supported:
            self._announcement_error("unsupported_robot_announcements")
        if self._quiet_hours():
            self._announcement_error("quiet_hours")
        if not robot.online:
            self._announcement_error("robot_offline")
        if (
            robot.busy
            or robot_id in self._robot_commands
            or any(pending.robot_id == robot_id for pending in self.pending_actions.values())
            or len(self.commands) + len(self.pending_actions) >= MAX_COMMANDS
        ):
            self._announcement_error("robot_busy")
        request_id = str(uuid4())
        deadline = self._server_now_ms() + ANNOUNCEMENT_SECONDS * 1000
        pending = PendingAction(robot_id, deadline, asyncio.get_running_loop().create_future())
        self.pending_actions[request_id] = pending
        start_time = asyncio.get_running_loop().time()
        result = error_result("confirmation_lost", "uncertain")
        try:
            async with asyncio.timeout(ANNOUNCEMENT_SECONDS):
                await self._send(
                    {
                        "type": "robot_action",
                        "request_id": request_id,
                        "robot_id": robot_id,
                        "action": "announce",
                        "text": message.strip(),
                        "deadline_ms": deadline,
                    }
                )
                result = await pending.future
        except asyncio.CancelledError:
            raise
        except ClientError, ConnectionError, RuntimeError, TimeoutError:
            pass  # Dispatch might have happened. This request is never retried.
        finally:
            self.pending_actions.pop(request_id, None)
            if not pending.future.done():
                pending.future.cancel()
            robot.last_response = "announcement"
            robot.last_outcome = result["outcome"]
            robot.last_error = result.get("code")
            robot.latency_ms = round((asyncio.get_running_loop().time() - start_time) * 1000, 1)
            self._notify()
        if result["outcome"] == "success":
            return result
        code = result.get("code", "confirmation_lost")
        code = {
            "offline": "robot_offline",
            "busy": "robot_busy",
            "forbidden": "permission_denied",
            "announcements_disabled": "permission_denied",
            "revoked": "permission_denied",
            "expired": "announcement_expired",
        }.get(code, code)
        if result["outcome"] == "uncertain":
            self._announcement_error("confirmation_lost", uncertain=True)
        if code not in ("robot_offline", "robot_busy", "permission_denied", "announcement_expired", "disconnected"):
            code = "announcement_failed"
        self._announcement_error(code)

    def _action_result(self, frame: dict[str, Any]) -> None:
        if "robot_action" not in self.capabilities:
            raise ValueError("Unnegotiated action result")
        request_id = str(UUID(frame["request_id"]))
        robot_id = str(UUID(frame["robot_id"]))
        result = frame.get("result")
        if not isinstance(result, dict) or result.get("outcome") not in ("success", "error", "uncertain", "expired"):
            raise ValueError("Invalid action result")
        if result.get("speech", "") != "" or not isinstance(result.get("code", ""), str):
            raise ValueError("Invalid action result")
        if not (pending := self.pending_actions.get(request_id)) or pending.future.done():
            return  # Late or duplicate confirmations can never trigger another action.
        if pending.robot_id != robot_id:
            raise ValueError("Invalid action correlation")
        if self._remaining(pending.deadline_ms) <= 0:
            pending.future.set_result(error_result("confirmation_lost", "uncertain"))
        else:
            pending.future.set_result(
                {
                    "outcome": result["outcome"],
                    "response_type": "action_done" if result["outcome"] == "success" else "error",
                    "speech": "",
                    **({"code": result["code"]} if result.get("code") else {}),
                }
            )

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
        self._robot_commands.clear()

    async def async_stop(self) -> None:
        self.stopping = True
        if self.socket is not None:
            await self.socket.close()
        await self._cancel_commands()
        self.ready = False
        self._clear_contexts()
        self._fail_actions()
        self.set_state("disconnected")
