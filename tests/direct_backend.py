"""Invented loopback robot: independent pairing formulas and real pinned TLS.

This public fixture contains no native BE implementation. An optional private
Node 6 fixture separately checks the actual endpoint before release.
"""

import asyncio
import hashlib
import hmac
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

from custom_components.phoenix._vendor.aiohomekit_srp import SrpServer

CAPABILITIES = [
    "robot_roster",
    "robot_action",
    "room_context",
    "state_queries",
    "follow_up",
    "routine_shortcuts",
    "telemetry",
]
PAIR_DOMAIN = "phoenix-local-pair-v2\n"
SUITE = "SRP6a-3072-SHA512"
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
    """Only fixture IPC may open pairing; HTTP cannot simulate touch."""

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
        self.tamper_challenge = False
        self.tamper_server_binding = False
        self.lose_finish_response = False
        self.status_unavailable = False
        self.pairing_code = None
        self.pairing_expires = 0
        self.pair_attempts = 0
        self.redirect = None
        self.identity_pairing_version = 2
        self.runner = None

    async def start(self):
        app = web.Application(client_max_size=8192)
        app.router.add_get(BASE + "/identity", self._identity)
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
        """Synthetic fixture IPC represents physical Start; HTTP cannot open it."""
        self.pairing_open = True
        self.pairing_code = f"{secrets.randbelow(100_000_000):08d}"
        self.pairing_expires = asyncio.get_running_loop().time() + 120
        self.pair_attempts = 0
        self.candidate = None
        return self.pairing_code

    async def _identity(self, request):
        self.http_requests.append((request.path, request.headers.get("Authorization"), {}))
        return web.json_response(
            {
                "v": 1,
                "pairing_version": self.identity_pairing_version,
                "robot_id": self.robot_id,
                "name": self.name,
                "firmware_version": "13.2.2",
            }
        )

    async def _pair(self, request):
        body = await request.json()
        self.http_requests.append((request.path, request.headers.get("Authorization"), body))
        step = request.match_info["step"]
        if body.get("v") != 2:
            return web.json_response({"v": 2, "code": "pairing_upgrade_required"}, status=400)
        now = asyncio.get_running_loop().time()
        if step == "begin":
            if not self.pairing_open or now >= self.pairing_expires:
                return web.json_response({"v": 2, "code": "pairing_expired"}, status=410)
            if self.candidate:
                return web.json_response({"v": 2, "code": "pairing_busy"}, status=409)
            if self.pair_attempts >= 5:
                return web.json_response({"v": 2, "code": "rate_limited"}, status=429)
            self.pair_attempts += 1
            assert body["fingerprint"] == self.fingerprint and body["robot_id"] == self.robot_id
            server = SrpServer(PAIR_DOMAIN + self.fingerprint + "\n" + self.robot_id, self.pairing_code)
            server.b = int.from_bytes(secrets.token_bytes(32), "big")
            server.set_client_public_key(bytes.fromhex(body["client_public"]))
            # The upstream server computes B dynamically. Refresh its padded B
            # after overriding the synthetic ephemeral exponent.
            server.B_b = server.get_public_key_bytes()
            server.B = int.from_bytes(server.B_b, "big")
            pair_id = str(uuid4())
            self.candidate = candidate = {
                **body,
                "pair_id": pair_id,
                "server": server,
                "salt": bytes(server.salt_b).hex(),
                "server_public": bytes(server.B_b).hex(),
                "generation": self.generation + 1,
                "name": self.name,
                "firmware_version": "13.2.2",
                "expires": min(now + 30, self.pairing_expires),
            }
            candidate["transcript"] = hashed(
                PAIR_DOMAIN
                + "transcript\n"
                + "\n".join(
                    (
                        SUITE,
                        self.fingerprint,
                        self.robot_id,
                        pair_id,
                        body["client_nonce"],
                        candidate["salt"],
                        body["client_public"],
                        candidate["server_public"],
                        str(candidate["generation"]),
                        self.name,
                        "13.2.2",
                    )
                )
            )
            return web.json_response(
                {
                    "v": 2,
                    "suite": SUITE,
                    "pair_id": pair_id,
                    "robot_id": self.robot_id,
                    "fingerprint": self.fingerprint,
                    "salt": candidate["salt"],
                    "server_public": "00" * 384 if self.tamper_challenge else candidate["server_public"],
                    "generation": candidate["generation"],
                    "name": self.name,
                    "firmware_version": "13.2.2",
                    "expires_in": 30,
                }
            )
        candidate = self.candidate
        if not candidate or candidate["pair_id"] != body.get("pair_id") or now >= candidate["expires"]:
            return web.json_response({"v": 2, "code": "pairing_expired"}, status=410)
        server = candidate["server"]
        key = server.get_session_key_bytes()

        def mac(value):
            return hmac.new(key, (PAIR_DOMAIN + value).encode(), hashlib.sha256).hexdigest()

        if step == "status":
            if self.status_unavailable:
                return web.json_response({"v": 2, "code": "pairing_unavailable"}, status=503)
            if body.get("recovery_proof") != mac("status\n" + candidate["transcript"]):
                return web.json_response({"v": 2, "code": "pairing_rejected"}, status=403)
            if "result" not in candidate:
                return web.json_response({"v": 2, "code": "pairing_incomplete"}, status=409)
            return web.json_response(candidate["result"])
        if step != "finish":
            return web.json_response({"v": 2, "code": "pairing_upgrade_required"}, status=400)
        proof = bytes.fromhex(body.get("client_proof", ""))
        if not server.verify_clients_proof_bytes(proof) or body.get("client_binding") != mac(
            "client\n" + candidate["transcript"]
        ):
            self.candidate = None
            return web.json_response({"v": 2, "code": "pairing_rejected"}, status=403)
        if "result" not in candidate:
            self.credential = mac("credential\n" + candidate["transcript"])
            self.generation = candidate["generation"]
            self.direct_enabled = True
            self.pairing_open = False
            self.pairing_code = None
            candidate["expires"] = self.pairing_expires
            await self.disconnect()
            server_proof = server.get_proof_bytes(proof).hex()
            candidate["result"] = {
                "v": 2,
                "suite": SUITE,
                "pair_id": candidate["pair_id"],
                "robot_id": self.robot_id,
                "generation": self.generation,
                "name": self.name,
                "firmware_version": "13.2.2",
                "server_proof": server_proof,
                "server_binding": "00" * 32
                if self.tamper_server_binding
                else mac("server\n" + candidate["transcript"] + "\n" + server_proof),
            }
        if self.lose_finish_response:
            self.lose_finish_response = False
            request.transport.close()
            return web.Response()
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
    """Exercise automatic one-form pairing with a synthetic physical code."""
    code = robot.open_pairing()
    finished = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": code},
    )
    assert finished["type"] == "create_entry", finished
    entry = finished["result"]
    await wait_for(lambda: hasattr(entry, "runtime_data") and entry.runtime_data.client.ready and robot.preferences)
    assert entry.unique_id == robot.robot_id and entry.version == 2
    return entry
