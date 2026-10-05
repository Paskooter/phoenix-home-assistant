"""Physical-pairing comparison, pinned TLS, guided migration and address changes."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import ClientSession
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from custom_components.phoenix.api import PhoenixError
from custom_components.phoenix.local_api import (
    authentication_string,
    begin_pairing,
    client_commitment,
    digest,
    finish_pairing,
    normalize_endpoint,
    server_commitment,
)
from tests.direct_backend import SyntheticLocalRobot, linked_entry, wait_for


def make_entry(data, *, version=2, options=None, unique_id=None):
    return ConfigEntry(
        version=version,
        minor_version=1,
        domain="phoenix",
        title="Invented Jibo",
        data=data,
        source="user",
        unique_id=unique_id or data.get("robot_id", str(uuid4())),
        options=options or {},
        discovery_keys={},
        subentries_data=None,
    )


def test_independent_committed_pairing_vector():
    claim_hash = digest("11" * 32)
    assert claim_hash == "3138bb9bc78df27c473ecfd1410f7bd45ebac1f59cf3ff9cfe4db77aab7aedd3"
    commitment = client_commitment("00" * 32, claim_hash)
    assert commitment == "28574c789a729ffc50c6abc8ac3ff29ca01848ead0ea50adb7fe5653afb05471"
    robot_id, pair_id = "22222222-2222-4222-8222-222222222222", "11111111-1111-4111-8111-111111111111"
    assert (
        server_commitment("33" * 32, robot_id, pair_id, commitment, claim_hash, "44" * 32)
        == "4356fb6bd5852e44018b598c9d16955a642c52a380ef3d3a2146c0628088fe97"
    )
    assert authentication_string("33" * 32, robot_id, pair_id, "00" * 32, "44" * 32, claim_hash) == "17791418"


async def test_pair_requires_both_robot_approval_and_owner_comparison(hass, robot):
    robot.open_pairing()
    result = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "user"}, data={"host": robot.host, "port": robot.port}
    )
    assert result["step_id"] == "pair_confirm"
    assert result["description_placeholders"]["code"] == robot.candidate["sas"]
    before = len(robot.http_requests)
    rejected = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm_pairing": False})
    assert rejected["errors"] == {"base": "codes_not_confirmed"}
    assert len(robot.http_requests) == before and robot.credential is None
    pending = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm_pairing": True})
    assert pending["errors"] == {"base": "pairing_pending"} and robot.credential is None
    robot.approve()
    finished = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm_pairing": True})
    assert finished["type"] == "create_entry"
    entry = finished["result"]
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert entry.data["fingerprint"] == robot.fingerprint and entry.unique_id == robot.robot_id
    assert entry.data["credential"] == robot.credential
    assert "claim_secret" not in json.dumps(entry.as_dict()) and "client_nonce" not in json.dumps(entry.as_dict())
    assert robot.direct_enabled and not robot.announcements_enabled


async def test_tampered_commitment_never_sends_secret_claim(hass, robot):
    robot.open_pairing()
    robot.tamper_commitment = True
    result = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "user"}, data={"host": robot.host, "port": robot.port}
    )
    assert result["errors"] == {"base": "pairing_verification_failed"}
    assert not any(path.endswith("finish") for path, _, _ in robot.http_requests)
    assert robot.credential is None and not hass.config_entries.async_entries("phoenix")


async def test_pairing_secret_is_independent_from_public_pair_id_and_nonce(hass, robot):
    robot.open_pairing()
    async with ClientSession() as session:
        candidate = await begin_pairing(session, normalize_endpoint(robot.host, robot.port))
        assert candidate.claim_secret != candidate.client_nonce and len(candidate.claim_secret) == 64
        robot.approve()
        linked = await finish_pairing(session, candidate)
    assert linked["generation"] == 1
    assert candidate.claim_secret not in linked["credential"] and candidate.client_nonce not in linked["credential"]


async def test_address_reconfigure_keeps_keys_and_pin(hass, robot):
    entry = await linked_entry(hass, robot)
    original = dict(entry.data)
    flow = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"host": robot.host, "port": robot.port})
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful", result
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert dict(entry.data) == original
    assert len([path for path, _, _ in robot.http_requests if "/pair/" in path]) == 3


async def test_address_reconfigure_changed_cert_never_sends_key_to_peer(hass, robot, tmp_path):
    entry = await linked_entry(hass, robot)
    original = dict(entry.data)
    other = await SyntheticLocalRobot(tmp_path / "untrusted-peer").start()
    try:
        flow = await hass.config_entries.flow.async_init(
            "phoenix", context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"host": other.host, "port": other.port}
        )
        assert result["errors"] == {"base": "certificate_changed"}
        assert other.http_requests == [] and dict(entry.data) == original
        assert entry.runtime_data.client.ready
    finally:
        await other.close()


async def test_legacy_migration_never_connects_cloud_and_explicitly_copies_area(hass, robot, monkeypatch):
    installation_id = str(uuid4())
    entry = make_entry(
        {
            "installation_id": installation_id,
            "credential": "invented-old-cloud-key",
            "phoenix_url": "https://cloud.invalid",
        },
        version=1,
        unique_id=installation_id,
        options={
            "conversation_agent": conversation.HOME_ASSISTANT_AGENT,
            "quiet_hours_enabled": True,
            "allow_announcements": True,
        },
    )
    await hass.config_entries.async_add(entry)
    assert entry.version == 2 and entry.data["transport"] == "pairing_required"
    assert entry.options["allow_announcements"] is False
    assert entry.runtime_data.client.socket is None
    await wait_for(lambda: entry.runtime_data.client.state == "pairing_required")
    area = ar.async_get(hass).async_create("Invented study")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("phoenix", installation_id + "/" + str(uuid4()))},
        name="Invented old robot",
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    store = Store(hass, 1, f"phoenix.{installation_id}.requests")
    await store.async_save({str(uuid4()): 42})
    revoked = []

    async def revoke(session, origin, credential):
        assert robot.credential is not None
        revoked.append((origin, credential))
        return False

    monkeypatch.setattr("custom_components.phoenix.config_flow.revoke", revoke)
    # The real migration setup started reauthentication; use that existing flow.
    flow = next(
        flow for flow in hass.config_entries.flow.async_progress() if flow["context"].get("entry_id") == entry.entry_id
    )
    hass.config_entries.flow.async_abort(flow["flow_id"])
    flow = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
    )
    robot.open_pairing()
    started = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"host": robot.host, "port": robot.port, "legacy_device_id": device.id}
    )
    assert started["step_id"] == "pair_confirm", started
    robot.approve()
    result = await hass.config_entries.flow.async_configure(started["flow_id"], {"confirm_pairing": True})
    assert result["type"] == "abort" and result["reason"] == "reauth_successful", result
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert entry.unique_id == robot.robot_id and entry.data["migration_area_id"] == area.id
    assert "installation_id" not in entry.data and entry.data["credential"] == robot.credential
    assert entry.options["quiet_hours_enabled"] is True and entry.options["allow_announcements"] is False
    assert entry.options["conversation_agent"] == conversation.HOME_ASSISTANT_AGENT
    assert revoked == [("https://cloud.invalid", "invented-old-cloud-key")]
    assert not Path(store.path).exists()
    assert dr.async_get(hass).async_get(entry.runtime_data.client.robots[robot.robot_id].device_id).area_id == area.id


async def test_address_same_cert_wrong_robot_does_not_replace_identity(hass, robot):
    entry = await linked_entry(hass, robot)
    original = dict(entry.data)
    robot.robot_id = str(uuid4())
    form = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(form["flow_id"], {"host": robot.host, "port": robot.port})
    assert result["errors"] == {"base": "wrong_robot"} and dict(entry.data) == original


async def test_expired_robot_pairing_window_cannot_issue_a_key(hass, robot):
    robot.open_pairing()
    form = await hass.config_entries.flow.async_init(
        "phoenix", context={"source": "user"}, data={"host": robot.host, "port": robot.port}
    )
    robot.approve()
    robot.candidate["expires"] = 0
    result = await hass.config_entries.flow.async_configure(form["flow_id"], {"confirm_pairing": True})
    assert result["errors"] == {"base": "pairing_expired"} and robot.credential is None


async def test_removing_inert_legacy_entry_attempts_cleanup_and_removes_ledger(hass, monkeypatch):
    installation_id = str(uuid4())
    entry = make_entry(
        {
            "transport": "pairing_required",
            "installation_id": installation_id,
            "credential": "invented-cloud-key",
            "phoenix_url": "https://cloud.invalid",
        },
        unique_id=installation_id,
    )
    await hass.config_entries.async_add(entry)
    store = Store(hass, 1, f"phoenix.{installation_id}.requests")
    await store.async_save({str(uuid4()): 42})
    revoked = []

    async def revoke(session, origin, credential):
        revoked.append((origin, credential))
        return False

    monkeypatch.setattr("custom_components.phoenix.revoke", revoke)
    await hass.config_entries.async_remove(entry.entry_id)
    assert revoked == [("https://cloud.invalid", "invented-cloud-key")] and not Path(store.path).exists()


async def test_version_two_missing_transport_requires_auth_without_cloud_socket(hass):
    entry = make_entry(
        {
            "installation_id": str(uuid4()),
            "credential": "invented-old-cloud-key",
            "phoenix_url": "https://cloud.invalid",
        }
    )
    await hass.config_entries.async_add(entry)
    await wait_for(lambda: entry.runtime_data.client.state == "authentication_required")
    assert entry.runtime_data.client.last_error == "invalid_pairing" and entry.runtime_data.client.socket is None


@pytest.mark.parametrize(
    "host", ["http://127.0.0.1", "user@host", "host/path", "host?query", "host#fragment", "host\nname"]
)
def test_manual_host_rejects_urls_and_control_fields(host):
    with pytest.raises(PhoenixError, match="invalid_host"):
        normalize_endpoint(host)
