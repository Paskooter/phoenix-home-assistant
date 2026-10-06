"""SRP-6a pairing with pinned upstream arithmetic and TLS identity binding."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from ._vendor.aiohomekit_srp import MODULUS_VALUE, SrpClient

DOMAIN = "phoenix-local-pair-v2\n"
SUITE = "SRP6a-3072-SHA512"
_PUBLIC = re.compile(r"^[0-9a-f]{768}$")
_PROOF = re.compile(r"^[0-9a-f]{128}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class PairingClient(SrpClient):
    """Use a 256-bit ephemeral exponent; validate remote values in the wrapper."""

    @staticmethod
    def generate_private_key() -> int:
        return int.from_bytes(secrets.token_bytes(32), "big")


def normalize_code(value: str) -> str:
    """A text field preserves leading zeroes; allow the displayed space."""
    if not isinstance(value, str):
        raise ValueError("invalid_pairing_code")
    value = value.strip().replace(" ", "").replace("-", "")
    if re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError("invalid_pairing_code")
    return value


def public_value(value: str) -> bytes:
    if not isinstance(value, str) or _PUBLIC.fullmatch(value) is None or not 0 < int(value, 16) < MODULUS_VALUE:
        raise ValueError("invalid_public_value")
    return bytes.fromhex(value)


def identity(fingerprint: str, robot_id: str) -> str:
    return DOMAIN + fingerprint + "\n" + robot_id


def transcript(values: dict[str, Any]) -> str:
    fields = (
        SUITE,
        values["fingerprint"],
        values["robot_id"],
        values["pair_id"],
        values["client_nonce"],
        values["salt"],
        values["client_public"],
        values["server_public"],
        str(values["generation"]),
        values["name"],
        values["firmware_version"],
    )
    return hashlib.sha256((DOMAIN + "transcript\n" + "\n".join(fields)).encode()).hexdigest()


def mac(key: bytes, value: str) -> str:
    return hmac.new(key, (DOMAIN + value).encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class PairCandidate:
    """Only the bounded SRP session survives between begin and finish."""

    endpoint: str
    pair_id: str
    robot_id: str
    fingerprint: str
    generation: int
    name: str
    firmware_version: str
    expires_at: float
    transcript: str
    session_key: bytes = field(repr=False)
    client_proof: str = field(repr=False)
    server_proof: str = field(repr=False)

    def finish_request(self) -> dict[str, str]:
        return {
            "pair_id": self.pair_id,
            "client_proof": self.client_proof,
            "client_binding": mac(self.session_key, "client\n" + self.transcript),
        }

    def status_request(self) -> dict[str, str]:
        return {"pair_id": self.pair_id, "recovery_proof": mac(self.session_key, "status\n" + self.transcript)}

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        """Confirm both SRP and transcript proofs before deriving a credential."""
        if (
            result.get("suite") != SUITE
            or result.get("pair_id") != self.pair_id
            or result.get("robot_id") != self.robot_id
            or type(result.get("generation")) is not int
            or result.get("generation") != self.generation
            or result.get("name") != self.name
            or result.get("firmware_version") != self.firmware_version
        ):
            raise ValueError("pairing_verification_failed")
        proof, binding = result.get("server_proof"), result.get("server_binding")
        if (
            not isinstance(proof, str)
            or _PROOF.fullmatch(proof) is None
            or not hmac.compare_digest(proof, self.server_proof)
            or not isinstance(binding, str)
            or _HEX.fullmatch(binding) is None
            or not hmac.compare_digest(binding, mac(self.session_key, "server\n" + self.transcript + "\n" + proof))
        ):
            raise ValueError("pairing_verification_failed")
        return {
            "robot_id": self.robot_id,
            "fingerprint": self.fingerprint,
            "generation": self.generation,
            "name": self.name,
            "firmware_version": self.firmware_version,
            "credential": mac(self.session_key, "credential\n" + self.transcript),
        }
