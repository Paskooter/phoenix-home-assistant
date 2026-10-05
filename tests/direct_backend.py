"""Invented loopback robot: independent pairing formulas and real pinned TLS.

This public fixture contains no native BE implementation. An optional private
Node 6 fixture separately checks the actual endpoint before release.
"""

import asyncio
import hashlib
import json
import secrets
import ssl
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from aiohttp import WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CAPABILITIES = [
    "robot_roster",
    "robot_action",
    "room_context",
    "state_queries",
    "follow_up",
    "routine_shortcuts",
    "telemetry",
]
PAIR_DOMAIN = "phoenix-local-pair-v1\n"
BASE = "/phoenix/local/v1"


def hashed(text):
    return hashlib.sha256(text.encode()).hexdigest()


def tls_identity(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Invented local robot")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    certificate = directory / "certificate.pem"
    private_key = directory / "private-key.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384")
    context.load_cert_chain(certificate, private_key)
    return context, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class SyntheticLocalRobot:
    """Only fixture IPC may open/approve pairing; HTTP cannot simulate touch."""

    def __init__(self, directory):
        self.context, self.fingerprint = tls_identity(directory)
        self.robot_id = str(uuid4())
        self.name = "Invented Jibo"
        self.generation = 0
        self.credential = None
        self.direct_enabled = False
        self.pairing_open = False
        self.candidate = None
        self.http_requests = []
        self.frames = []
        self.sent = []
        self.speech_calls = []
        self.action_outcome = "success"
        self.online = True
        self.busy = False
        self.supported = True
        self.announcements_enabled = False
        self.auto_pong = True
        self.socket = None
        self.session_id = None
        self.preferences = {}
        self.connections = 0
        self.waiters = {}
        self.tamper_commitment = False
        self.redirect = None
        self.runner = None

    async def start(self):
        app = web.Application(client_max_size=8192)
        app.router.add_post(BASE + "/pair/{step}", self._pair)
        app.router.add_delete(BASE + "/pairing", self._revoke)
        app.router.add_get(BASE + "/connect", self._connect)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0, ssl_context=self.context)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.host = "127.0.0.1"
        return self

    async def close(self):
        await self.disconnect()
        if self.runner:
            await self.runner.cleanup()

    def open_pairing(self):
        self.pairing_open = True
        self.candidate = None

    def approve(self):
        assert self.candidate and self.candidate.get("sas")
        self.candidate["approved"] = True

    async def _pair(self, request):
        body = await request.json()
        self.http_requests.append((request.path, request.headers.get("Authorization"), body))
        if not self.pairing_open:
            return web.json_response({"v": 1, "code": "pairing_unavailable"}, status=410)
        step = request.match_info["step"]
        if step == "begin":
            if self.candidate:
                return web.json_response({"v": 1, "code": "pairing_unavailable"}, status=409)
            pair_id, server_nonce = str(uuid4()), secrets.token_hex(32)
            self.candidate = {
                **body,
                "pair_id": pair_id,
                "server_nonce": server_nonce,
                "expires": asyncio.get_running_loop().time() + 120,
                "approved": False,
            }
            commitment = hashed(
                PAIR_DOMAIN
                + "server\n"
                + "\n".join(
                    (
                        self.fingerprint,
                        self.robot_id,
                        pair_id,
                        body["client_commitment"],
                        body["claim_hash"],
                        server_nonce,
                    )
                )
            )
            return web.json_response(
                {
                    "v": 1,
                    "pair_id": pair_id,
                    "robot_id": self.robot_id,
                    "fingerprint": self.fingerprint,
                    "server_commitment": "00" * 32 if self.tamper_commitment else commitment,
                    "expires_in": 120,
                }
            )
        candidate = self.candidate
        if (
            not candidate
            or candidate["pair_id"] != body.get("pair_id")
            or candidate["expires"] < asyncio.get_running_loop().time()
        ):
            return web.json_response({"v": 1, "code": "pairing_expired"}, status=410)
        if step == "reveal":
            client_nonce = body["client_nonce"]
            if (
                hashed(PAIR_DOMAIN + "client\n" + client_nonce + "\n" + candidate["claim_hash"])
                != candidate["client_commitment"]
            ):
                return web.json_response({"v": 1, "code": "invalid_claim"}, status=403)
            sas_hash = hashed(
                PAIR_DOMAIN
                + "sas\n"
                + "\n".join(
                    (
                        self.fingerprint,
                        self.robot_id,
                        candidate["pair_id"],
                        client_nonce,
                        candidate["server_nonce"],
                        candidate["claim_hash"],
                    )
                )
            )
            candidate["sas"] = f"{int(sas_hash[:8], 16) % 100_000_000:08d}"
            return web.json_response(
                {"v": 1, "pair_id": candidate["pair_id"], "server_nonce": candidate["server_nonce"]}
            )
        if step != "finish" or hashed(body.get("claim_secret", "")) != candidate["claim_hash"]:
            return web.json_response({"v": 1, "code": "invalid_claim"}, status=403)
        if not candidate["approved"]:
            return web.json_response({"v": 1, "code": "pairing_pending"}, status=409)
        if "result" not in candidate:
            self.credential = secrets.token_hex(32)
            self.generation += 1
            self.direct_enabled = True
            await self.disconnect()
            candidate["result"] = {
                "v": 1,
                "robot_id": self.robot_id,
                "credential": self.credential,
                "generation": self.generation,
                "name": self.name,
                "firmware_version": "13.2.0",
            }
        return web.json_response(candidate["result"])

    def _authorized(self, request):
        return self.credential is not None and request.headers.get("Authorization") == "Bearer " + self.credential

    async def _revoke(self, request):
        self.http_requests.append((request.path, request.headers.get("Authorization"), {}))
        if not self._authorized(request):
            return web.Response(status=401)
        self.credential = None
        self.generation += 1
        await self.disconnect(4001)
        return web.Response(status=204)

    async def _connect(self, request):
        self.http_requests.append((request.path, request.headers.get("Authorization"), {}))
        if self.redirect:
            raise web.HTTPFound(location=self.redirect)
        if not self._authorized(request):
            return web.Response(status=401)
        await self.disconnect(4002)
        socket = web.WebSocketResponse(max_msg_size=8192, autoping=self.auto_pong)
        await socket.prepare(request)
        self.socket = socket
        self.connections += 1
        self.session_id = str(uuid4())
        self.announcements_enabled = False
        self.preferences = {}
        await self.send(
            {
                "type": "welcome",
                "robot_id": self.robot_id,
                "server_time_ms": self.now_ms(),
                "capabilities": CAPABILITIES,
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
                    assert isinstance(frame["announcements_enabled"], bool)
                    self.preferences = frame
                    self.announcements_enabled = frame["announcements_enabled"]
                    await self.roster()
                elif frame["type"] == "accepted":
                    assert frame["robot_id"] == self.robot_id
                elif frame["type"] == "result":
                    if future := self.waiters.get(frame["request_id"]):
                        if not future.done():
                            future.set_result(frame["result"])
                elif frame["type"] == "robot_action":
                    if "volume" in frame:
                        outcome, code = "error", "unsupported_volume"
                    elif not self.announcements_enabled:
                        outcome, code = "error", "forbidden"
                    else:
                        self.speech_calls.append(frame)
                        outcome, code = self.action_outcome, None
                    if outcome == "hold":
                        continue
                    await self.send(
                        {
                            "type": "action_result",
                            "request_id": frame["request_id"],
                            "robot_id": self.robot_id,
                            "result": {"outcome": outcome, "speech": "", **({"code": code} if code else {})},
                        }
                    )
                else:
                    raise AssertionError("Unexpected invented-peer frame")
        finally:
            if self.socket is socket:
                self.socket = None
        return socket

    def now_ms(self):
        return int(time.time() * 1000)

    async def send(self, frame):
        outgoing = {"v": 2, "session_id": self.session_id, "generation": self.generation, **frame}
        self.sent.append(outgoing)
        await self.socket.send_json(outgoing)

    async def roster(self):
        await self.send(
            {
                "type": "roster",
                "robots": [
                    {
                        "robot_id": self.robot_id,
                        "name": self.name,
                        "online": self.online,
                        "busy": self.busy,
                        "announcements_allowed": self.announcements_enabled,
                        "announcements_supported": self.supported,
                    }
                ],
            }
        )

    async def telemetry(self, values, *, age_ms=0, observed_at_ms=None):
        await self.send(
            {
                "type": "telemetry",
                "robot_id": self.robot_id,
                "observed_at_ms": self.now_ms() - age_ms if observed_at_ms is None else observed_at_ms,
                "values": values,
            }
        )

    async def command(self, text, *, route=None, request_id=None, deadline_seconds=7.5, **extra):
        request_id = request_id or str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.waiters[request_id] = future
        started = time.perf_counter()
        await self.send(
            {
                "type": "command",
                "robot_id": self.robot_id,
                "request_id": request_id,
                "text": text,
                "language": "en",
                "route": route or {"kind": "command"},
                "deadline_ms": self.now_ms() + int(deadline_seconds * 1000),
                **extra,
            }
        )
        try:
            async with asyncio.timeout(12):
                return await future, (time.perf_counter() - started) * 1000
        finally:
            self.waiters.pop(request_id, None)

    async def disconnect(self, code=1001):
        if self.socket is not None:
            socket, self.socket = self.socket, None
            await socket.close(code=code)


async def wait_for(predicate, seconds=8):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def linked_entry(hass, robot):
    """Exercise the real user flow, compare both independent codes and approve."""
    robot.open_pairing()
    started = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "user"}, data={"host": robot.host, "port": robot.port}
    )
    assert started["type"] == "form" and started["step_id"] == "pair_confirm", started
    assert started["description_placeholders"]["code"] == robot.candidate["sas"]
    assert robot.credential is None
    robot.approve()
    finished = await hass.config_entries.flow.async_configure(started["flow_id"], {"confirm_pairing": True})
    assert finished["type"] == "create_entry", finished
    entry = finished["result"]
    await wait_for(lambda: hasattr(entry, "runtime_data") and entry.runtime_data.client.ready and robot.preferences)
    assert entry.unique_id == robot.robot_id and entry.version == 2
    return entry
