"""Physical one-time-code SRP pairing and pinned TLS; no cloud authorization."""

import asyncio
import hashlib
import ipaddress
import json
import re
import secrets
import ssl
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
from .local_pairing import SUITE, PairCandidate, PairingClient, identity, normalize_code, public_value, transcript

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
                        "pairing_busy",
                        "pairing_incomplete",
                        "pairing_upgrade_required",
                        "local_storage_unavailable",
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


async def _pair_identity(session: ClientSession, endpoint: str, fingerprint: str) -> dict[str, Any]:
    """An untrusted identity is authenticated by the subsequent SRP proofs."""
    try:
        async with asyncio.timeout(8):
            async with session.get(
                endpoint + "/phoenix/local/v1/identity",
                ssl=LocalFingerprint(bytes.fromhex(fingerprint)),
                allow_redirects=False,
            ) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(MAX_FRAME_BYTES + 1):
                    raw.extend(chunk)
                    if len(raw) > MAX_FRAME_BYTES:
                        raise PhoenixError("unsupported_protocol")
                data = json.loads(raw)
                if response.status != 200 or not isinstance(data, dict) or type(data.get("v")) is not int:
                    raise PhoenixError("unsupported_protocol")
                if (
                    data.get("v") != 1
                    or type(data.get("pairing_version")) is not int
                    or data.get("pairing_version") != 2
                ):
                    raise PhoenixError("pairing_upgrade_required")
                return data
    except ServerFingerprintMismatch as err:
        raise PhoenixError("certificate_changed") from err
    except LocalTLSRejected as err:
        raise PhoenixError("unsupported_tls") from err
    except (ClientError, OSError, TimeoutError, ValueError) as err:
        raise PhoenixError("cannot_connect") from err


def _pair_name(value: Any, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid_name")
    return value


async def begin_pairing(session: ClientSession, endpoint: str, code: str) -> PairCandidate:
    """The physical code stays local. No operational key exists before proof."""
    try:
        code = normalize_code(code)
    except ValueError as err:
        raise PhoenixError("invalid_pairing_code") from err
    fingerprint = await capture_fingerprint(endpoint)
    initial = await _pair_identity(session, endpoint, fingerprint)
    try:
        robot_id = canonical_uuid(initial["robot_id"])
        name = _pair_name(initial["name"], 100)
        firmware = _pair_name(initial["firmware_version"], 32)
    except (ValueError, KeyError, TypeError) as err:
        raise PhoenixError("unsupported_protocol") from err
    client = await asyncio.to_thread(PairingClient, identity(fingerprint, robot_id), code)
    client_nonce = secrets.token_hex(32)
    request = {
        "robot_id": robot_id,
        "fingerprint": fingerprint,
        "client_nonce": client_nonce,
        "client_public": client.get_public_key_bytes().hex(),
    }
    started = asyncio.get_running_loop().time()
    challenge = await _pair_post(session, endpoint, "begin", request, fingerprint)
    try:
        pair_id = canonical_uuid(challenge["pair_id"])
        generation, expires = challenge["generation"], challenge["expires_in"]
        if (
            challenge.get("suite") != SUITE
            or challenge.get("robot_id") != robot_id
            or challenge.get("fingerprint") != fingerprint
            or challenge.get("name") != name
            or challenge.get("firmware_version") != firmware
            or type(generation) is not int
            or not 1 <= generation <= 2**53 - 1
            or type(expires) is not int
            or not 1 <= expires <= 120
            or not isinstance(challenge.get("salt"), str)
            or re.fullmatch(r"[0-9a-f]{32}", challenge["salt"]) is None
        ):
            raise ValueError("invalid_challenge")
        server_public = public_value(challenge["server_public"])
        client.set_salt(bytearray.fromhex(challenge["salt"]))
        client.set_server_public_key(server_public)
        if client._calculate_u() == 0:
            raise ValueError("invalid_scrambling_parameter")
        proof = await asyncio.to_thread(client.get_proof_bytes)
        key = client.get_session_key_bytes()
        expected_server_proof = client.digest(client.get_public_key_bytes(), proof, key).hex()
        candidate = PairCandidate(
            endpoint,
            pair_id,
            robot_id,
            fingerprint,
            generation,
            name,
            firmware,
            started + expires,
            transcript({**challenge, **request}),
            key,
            proof.hex(),
            expected_server_proof,
        )
    except (ValueError, TypeError, KeyError, RuntimeError) as err:
        raise PhoenixError("pairing_verification_failed") from err
    finally:
        client.password = ""
    return candidate


async def finish_pairing(session: ClientSession, candidate: PairCandidate) -> dict[str, Any]:
    """Finish once; a lost response is recovered by a read-only status query."""
    if asyncio.get_running_loop().time() >= candidate.expires_at:
        raise PhoenixError("pairing_expired")
    try:
        body = await _pair_post(
            session, candidate.endpoint, "finish", candidate.finish_request(), candidate.fingerprint
        )
    except PhoenixError as err:
        if err.code != "cannot_connect":
            raise
        try:
            body = await _pair_post(
                session, candidate.endpoint, "status", candidate.status_request(), candidate.fingerprint
            )
        except PhoenixError as recovery:
            raise PhoenixError("pairing_confirmation_lost") from recovery
    try:
        return candidate.verify(body)
    except (ValueError, KeyError, TypeError) as err:
        raise PhoenixError("pairing_verification_failed") from err


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
