"""Pinned source and cross-language HomeKit SRP encoding invariants."""

import hashlib
import json
from pathlib import Path

import pytest

from custom_components.phoenix._vendor.aiohomekit_srp import MODULUS_VALUE, SrpServer
from custom_components.phoenix.local_pairing import PairingClient, normalize_code, public_value


def test_vendored_srp_and_license_match_upstream_hashes():
    directory = Path(__file__).parents[1] / "custom_components/phoenix/_vendor"
    provenance = json.loads((directory / "provenance.json").read_text())
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("value", ["00" * 384, f"{MODULUS_VALUE:0768x}", "ff" * 384, "01", "gg" * 384])
def test_remote_srp_values_are_canonical_and_within_the_known_group(value):
    with pytest.raises(ValueError, match="invalid_public_value"):
        public_value(value)


def test_code_is_text_and_preserves_a_leading_zero():
    assert normalize_code("0123 4567") == "01234567"
    assert normalize_code("0123-4567") == "01234567"
    for value in (1234567, "1234567", "１２３４５６７８", "01234567\nmore"):
        with pytest.raises(ValueError, match="invalid_pairing_code"):
            normalize_code(value)


def test_upstream_leading_zero_public_key_and_salt_vector_retains_fixed_width():
    # Fixed public upstream regression input. Short exponents are used solely
    # for this encoding vector; PairingClient uses 256 random bits in production.
    class LeadingZeroClient(PairingClient):
        @staticmethod
        def generate_private_key():
            return 70997313118674976963008287637113704817

    class ZeroSaltServer(SrpServer):
        def _create_salt_bytes(self):
            return b"\x00" * 16

    client = LeadingZeroClient("Pair-Setup", "123-45-678")
    server = ZeroSaltServer("Pair-Setup", "123-45-678")
    assert len(client.get_public_key_bytes()) == 384 and client.get_public_key_bytes()[0] == 0
    client.set_salt(bytearray(server.salt_b))
    client.set_server_public_key(public_value(server.get_public_key_bytes().hex()))
    server.set_client_public_key(client.get_public_key_bytes())
    proof = client.get_proof_bytes()
    assert server.verify_clients_proof_bytes(proof)
    assert client.verify_servers_proof_bytes(server.get_proof_bytes(proof))
    assert client.get_session_key_bytes() == server.get_session_key_bytes()
