"""Physically confirmed local pairing and pinned TLS; no cloud authorization."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import ssl
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from aiohttp import (
    ClientError,
    ClientHandlerType,
    ClientRequest,
    ClientResponse,
    ClientSession,
    ClientWSTimeout,
    Fingerprint,
    ServerFingerprintMismatch,
    WSServerHandshakeError,
)
from yarl import URL

from .api import PhoenixError
from .const import LOCAL_PORT, MAX_FRAME_BYTES, PAIRING_VERSION, PROTOCOL_VERSION

PAIR_DOMAIN = "phoenix-local-pair-v1\n"
PAIR_PATH = "/phoenix/local/v1/pair/"
LOCAL_CIPHERS = ("ECDHE-RSA-AES128-GCM-SHA256", "ECDHE-RSA-AES256-GCM-SHA384")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class LocalTLSRejected(ClientError):
    """The peer did not negotiate the required encrypted local transport."""


async def reject_redirects(request: ClientRequest, handler: ClientHandlerType) -> ClientResponse:
    """Pinning never authorizes a redirect to another address or origin."""
    response = await handler(request)
    if response.status in (301, 302, 303, 307, 308):
        response.close()
        raise ClientError("Local connector redirects are not allowed")
    return response


class LocalFingerprint(Fingerprint):
    """Validate pin and TLS before aiohttp writes any request headers/body."""

    def check(self, transport: asyncio.Transport) -> None:
        tls = transport.get_extra_info("ssl_object")
        if tls is None or tls.version() != "TLSv1.2" or tls.cipher()[0] not in LOCAL_CIPHERS:
            raise LocalTLSRejected("Unsupported local TLS")
        super().check(transport)


def hexadecimal(value: Any) -> str:
    """Protocol secrets and hashes are exactly 32 bytes, lowercase hex."""
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError("Invalid hex value")
    return value


def canonical_uuid(value: Any) -> str:
    """Accept only the wire's canonical lowercase v4 identities."""
    if not isinstance(value, str):
        raise ValueError("Invalid UUID")
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Invalid UUID")
    return value


def normalize_endpoint(host: str, port: int = LOCAL_PORT) -> str:
    """An owner-supplied host, never a URL with credentials or arbitrary paths."""
    try:
        if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError
        host = host.strip().lower()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            if _HOST.fullmatch(host) is None or ".." in host or any(not label for label in host.split(".")):
                raise ValueError from None
        return str(URL.build(scheme="https", host=host, port=port).origin())
    except (ValueError, TypeError, AttributeError) as err:
        raise PhoenixError("invalid_host") from err


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def client_commitment(client_nonce: str, claim_hash: str) -> str:
    return digest(PAIR_DOMAIN + "client\n" + hexadecimal(client_nonce) + "\n" + hexadecimal(claim_hash))


def server_commitment(
    fingerprint: str, robot_id: str, pair_id: str, commitment: str, claim_hash: str, server_nonce: str
) -> str:
    return digest(
        PAIR_DOMAIN
        + "server\n"
        + "\n".join(
            (
                hexadecimal(fingerprint),
                canonical_uuid(robot_id),
                canonical_uuid(pair_id),
                hexadecimal(commitment),
                hexadecimal(claim_hash),
                hexadecimal(server_nonce),
            )
        )
    )


def authentication_string(
    fingerprint: str, robot_id: str, pair_id: str, client_nonce: str, server_nonce: str, claim_hash: str
) -> str:
    hashed = digest(
        PAIR_DOMAIN
        + "sas\n"
        + "\n".join(
            (
                hexadecimal(fingerprint),
                canonical_uuid(robot_id),
                canonical_uuid(pair_id),
                hexadecimal(client_nonce),
                hexadecimal(server_nonce),
                hexadecimal(claim_hash),
            )
        )
    )
    return f"{int(hashed[:8], 16) % 100_000_000:08d}"


@dataclass(frozen=True)
class PairCandidate:
    """A bounded in-memory exchange; no operational key exists before approval."""

    endpoint: str
    pair_id: str
    robot_id: str
    fingerprint: str
    client_nonce: str = field(repr=False)
    claim_secret: str = field(repr=False)
    sas: str = field(repr=False)
    expires_at: float


async def capture_fingerprint(endpoint: str) -> str:
    """Make a TLS-only probe. Send no HTTP, secrets or household data."""
    url = URL(endpoint)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(":".join(LOCAL_CIPHERS))
    writer = None
    try:
        async with asyncio.timeout(5):
            _, writer = await asyncio.open_connection(url.host, url.port, ssl=context, server_hostname=url.host)
            tls = writer.get_extra_info("ssl_object")
            certificate = tls.getpeercert(binary_form=True) if tls else None
            if not certificate or tls.version() != "TLSv1.2" or tls.cipher()[0] not in LOCAL_CIPHERS:
                raise PhoenixError("unsupported_tls")
            return hashlib.sha256(certificate).hexdigest()
    except (OSError, TimeoutError, ValueError) as err:
        raise PhoenixError("cannot_connect") from err
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except OSError, TimeoutError:
                pass


async def _pair_post(session: ClientSession, endpoint: str, method: str, data: dict, fingerprint: str) -> dict:
    """Every pairing HTTP request is pinned to the probed certificate."""
    try:
        async with asyncio.timeout(8):
            async with session.post(
                endpoint + PAIR_PATH + method,
                json={"v": PAIRING_VERSION, **data},
                ssl=LocalFingerprint(bytes.fromhex(hexadecimal(fingerprint))),
                allow_redirects=False,
            ) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(MAX_FRAME_BYTES + 1):
                    raw.extend(chunk)
                    if len(raw) > MAX_FRAME_BYTES:
                        raise PhoenixError("unsupported_protocol")
                body = json.loads(raw)
                if not isinstance(body, dict) or type(body.get("v")) is not int or body.get("v") != PAIRING_VERSION:
                    raise PhoenixError("unsupported_protocol")
                if response.status != 200:
                    code = body.get("code")
                    if code not in (
                        "pairing_pending",
                        "pairing_expired",
                        "pairing_rejected",
                        "pairing_unavailable",
                        "invalid_claim",
                        "rate_limited",
                    ):
                        code = "pairing_failed"
                    raise PhoenixError(code)
                return body
    except ServerFingerprintMismatch as err:
        raise PhoenixError("certificate_changed") from err
    except LocalTLSRejected as err:
        raise PhoenixError("unsupported_tls") from err
    except (ClientError, OSError, TimeoutError, ValueError) as err:
        raise PhoenixError("cannot_connect") from err


async def begin_pairing(session: ClientSession, endpoint: str) -> PairCandidate:
    """Freeze both commitments before either nonce is revealed."""
    client_nonce, claim_secret = secrets.token_hex(32), secrets.token_hex(32)
    claim_hash = digest(claim_secret)
    commitment = client_commitment(client_nonce, claim_hash)
    fingerprint = await capture_fingerprint(endpoint)
    started = asyncio.get_running_loop().time()
    initial = await _pair_post(
        session, endpoint, "begin", {"client_commitment": commitment, "claim_hash": claim_hash}, fingerprint
    )
    try:
        pair_id, robot_id = canonical_uuid(initial["pair_id"]), canonical_uuid(initial["robot_id"])
        if (
            initial.get("fingerprint") != fingerprint
            or type(initial.get("expires_in")) is not int
            or initial.get("expires_in") != 120
        ):
            raise ValueError
        committed = hexadecimal(initial["server_commitment"])
        reveal = await _pair_post(
            session, endpoint, "reveal", {"pair_id": pair_id, "client_nonce": client_nonce}, fingerprint
        )
        if reveal.get("pair_id") != pair_id:
            raise ValueError
        server_nonce = hexadecimal(reveal["server_nonce"])
        expected = server_commitment(fingerprint, robot_id, pair_id, commitment, claim_hash, server_nonce)
        if not hmac.compare_digest(committed, expected):
            raise PhoenixError("pairing_verification_failed")
        sas = authentication_string(fingerprint, robot_id, pair_id, client_nonce, server_nonce, claim_hash)
    except (ValueError, TypeError, KeyError) as err:
        raise PhoenixError("pairing_verification_failed") from err
    return PairCandidate(endpoint, pair_id, robot_id, fingerprint, client_nonce, claim_secret, sas, started + 120)


async def finish_pairing(session: ClientSession, candidate: PairCandidate) -> dict[str, Any]:
    """Owner-triggered only. A lost finish may be queried again with this claim."""
    if asyncio.get_running_loop().time() >= candidate.expires_at:
        raise PhoenixError("pairing_expired")
    body = await _pair_post(
        session,
        candidate.endpoint,
        "finish",
        {"pair_id": candidate.pair_id, "claim_secret": candidate.claim_secret},
        candidate.fingerprint,
    )
    try:
        if canonical_uuid(body["robot_id"]) != candidate.robot_id:
            raise ValueError
        credential = hexadecimal(body["credential"])
        generation = body["generation"]
        name, firmware = body["name"], body["firmware_version"]
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or not isinstance(name, str)
            or not 1 <= len(name) <= 100
            or any(ord(char) < 32 for char in name)
            or not isinstance(firmware, str)
            or not 1 <= len(firmware) <= 32
            or any(ord(char) < 32 for char in firmware)
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError) as err:
        raise PhoenixError("unsupported_protocol") from err
    return {
        "robot_id": candidate.robot_id,
        "fingerprint": candidate.fingerprint,
        "credential": credential,
        "generation": generation,
        "name": name,
        "firmware_version": firmware,
    }


async def revoke_pairing(session: ClientSession, endpoint: str, fingerprint: str, credential: str) -> bool:
    """One pinned revocation attempt; offline removal requires on-robot Forget."""
    try:
        async with asyncio.timeout(5):
            async with session.delete(
                endpoint + "/phoenix/local/v1/pairing",
                headers={"Authorization": f"Bearer {hexadecimal(credential)}"},
                ssl=LocalFingerprint(bytes.fromhex(hexadecimal(fingerprint))),
                allow_redirects=False,
            ) as response:
                return response.status in (200, 204, 401)
    except ClientError, OSError, TimeoutError, ValueError:
        return False


async def verify_pairing(
    session: ClientSession, endpoint: str, fingerprint: str, credential: str, robot_id: str, generation: int
) -> None:
    """Changing an address must authenticate the same stored identity and key."""
    try:
        async with asyncio.timeout(8):
            # Share HA's managed connector, but own a redirect-rejecting session.
            async with (
                ClientSession(
                    connector=session.connector, connector_owner=False, middlewares=(reject_redirects,)
                ) as guarded_session,
                guarded_session.ws_connect(
                    endpoint.replace("https://", "wss://", 1) + "/phoenix/local/v1/connect",
                    headers={"Authorization": f"Bearer {hexadecimal(credential)}"},
                    ssl=LocalFingerprint(bytes.fromhex(hexadecimal(fingerprint))),
                    timeout=ClientWSTimeout(ws_close=1),
                    max_msg_size=MAX_FRAME_BYTES,
                ) as socket,
            ):
                welcome = await socket.receive_json()
                if (
                    not isinstance(welcome, dict)
                    or welcome.get("v") != PROTOCOL_VERSION
                    or welcome.get("type") != "welcome"
                    or welcome.get("robot_id") != canonical_uuid(robot_id)
                    or welcome.get("generation") != generation
                    or not isinstance(welcome.get("generation"), int)
                    or isinstance(welcome.get("generation"), bool)
                ):
                    raise PhoenixError("wrong_robot")
                canonical_uuid(welcome["session_id"])
                # Never send ready, preferences, commands or announcements here.
    except ServerFingerprintMismatch as err:
        raise PhoenixError("certificate_changed") from err
    except LocalTLSRejected as err:
        raise PhoenixError("unsupported_tls") from err
    except WSServerHandshakeError as err:
        raise PhoenixError("invalid_auth" if err.status in (401, 403) else "cannot_connect") from err
    except (ClientError, OSError, TimeoutError, ValueError, TypeError, KeyError) as err:
        raise PhoenixError("cannot_connect") from err
