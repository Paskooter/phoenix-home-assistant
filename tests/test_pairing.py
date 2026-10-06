"""Single-form physical-code SRP pairing, pinned TLS and preserved credentials."""

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
    begin_pairing,
    finish_pairing,
    normalize_endpoint,
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


async def test_physical_code_completes_one_form_without_secondary_approval(hass, robot):
    unavailable = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": "01234567"},
    )
    assert unavailable["errors"] == {"base": "pairing_expired"}
    assert robot.credential is None
    code = robot.open_pairing()
    finished = await hass.config_entries.flow.async_configure(
        unavailable["flow_id"],
        {
            "host": robot.host,
            "port": robot.port,
            "connection_code": code,
        },
    )
    assert finished["type"] == "create_entry"
    entry = finished["result"]
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert entry.data["fingerprint"] == robot.fingerprint and entry.unique_id == robot.robot_id
    assert entry.data["credential"] == robot.credential
    assert "connection_code" not in entry.data and "session_key" not in entry.data
    assert "client_proof" not in entry.data and "claim_secret" not in entry.data
    assert robot.direct_enabled and not robot.announcements_enabled
    assert robot.pairing_code is None and not robot.pairing_open
    assert len([path for path, _, _ in robot.http_requests if "/pair/finish" in path]) == 1


async def test_tampered_srp_challenge_never_sends_a_finish(hass, robot):
    code = robot.open_pairing()
    robot.tamper_challenge = True
    result = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": code},
    )
    assert result["errors"] == {"base": "pairing_verification_failed"}
    assert not any(path.endswith("finish") for path, _, _ in robot.http_requests)
    assert robot.credential is None and not hass.config_entries.async_entries("phoenix")


async def test_pairing_code_and_operational_credential_never_appear_in_pair_http_bodies(hass, robot):
    code = robot.open_pairing()
    async with ClientSession() as session:
        candidate = await begin_pairing(session, normalize_endpoint(robot.host, robot.port), code)
        assert len(candidate.session_key) == 64
        assert "session_key=" not in repr(candidate) and "client_proof=" not in repr(candidate)
        linked = await finish_pairing(session, candidate)
    assert linked["generation"] == 1 and linked["credential"] == robot.credential
    for path, authorization, body in robot.http_requests:
        assert authorization is None
        assert not {"code", "connection_code", "credential", "password", "claim_secret", "session_key"} & body.keys()
    assert candidate.verify(robot.candidate["result"]) == linked


async def test_wrong_code_can_retry_the_same_form_and_never_issues_a_credential(hass, robot):
    code = robot.open_pairing()
    wrong = "00000000" if code != "00000000" else "11111111"
    failed = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": wrong},
    )
    assert failed["errors"] == {"base": "pairing_rejected"}
    assert not hass.config_entries.async_entries("phoenix") and robot.credential is None
    finished = await hass.config_entries.flow.async_configure(
        failed["flow_id"],
        {
            "host": robot.host,
            "port": robot.port,
            "connection_code": code,
        },
    )
    assert finished["type"] == "create_entry" and robot.generation == 1


async def test_invalid_code_is_rejected_before_tls_or_http(hass, robot):
    result = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": "1234567"},
    )
    assert result["errors"] == {"base": "invalid_pairing_code"} and robot.http_requests == []


async def test_tampered_server_binding_never_saves_an_operational_key(hass, robot):
    code = robot.open_pairing()
    robot.tamper_server_binding = True
    result = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": code},
    )
    assert result["errors"] == {"base": "pairing_verification_failed"}
    assert not hass.config_entries.async_entries("phoenix")


async def test_lost_finish_is_recovered_without_another_pairing_mutation(hass, robot):
    code = robot.open_pairing()
    robot.lose_finish_response = True
    result = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": code},
    )
    assert result["type"] == "create_entry", result
    assert robot.generation == 1
    steps = [path.rsplit("/", 1)[-1] for path, _, _ in robot.http_requests if "/pair/" in path]
    assert steps == ["begin", "finish", "status"]


async def test_unknown_pairing_outcome_is_reported_without_reissuing_a_claim(hass, robot):
    code = robot.open_pairing()
    robot.lose_finish_response = robot.status_unavailable = True
    result = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": code},
    )
    assert result["errors"] == {"base": "pairing_confirmation_lost"}
    assert robot.generation == 1 and not hass.config_entries.async_entries("phoenix")
    assert len([path for path, _, _ in robot.http_requests if path.endswith("finish")]) == 1


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
    assert len([path for path, _, _ in robot.http_requests if "/pair/" in path]) == 2


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
    code = robot.open_pairing()
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {"host": robot.host, "port": robot.port, "connection_code": code, "legacy_device_id": device.id},
    )
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
    code = robot.open_pairing()
    async with ClientSession() as session:
        candidate = await begin_pairing(session, normalize_endpoint(robot.host, robot.port), code)
        robot.candidate["expires"] = 0
        with pytest.raises(PhoenixError, match="pairing_expired"):
            await finish_pairing(session, candidate)
    assert robot.credential is None


async def test_pairing_version_one_is_never_negotiated_as_a_downgrade(hass, robot):
    code = robot.open_pairing()
    robot.identity_pairing_version = 1
    result = await hass.config_entries.flow.async_init(
        "phoenix",
        context={"source": "user"},
        data={"host": robot.host, "port": robot.port, "connection_code": code},
    )
    assert result["errors"] == {"base": "pairing_upgrade_required"}
    assert not any("/pair/" in path for path, _, _ in robot.http_requests)


async def test_upgrade_retains_an_existing_local_v1_pairing_credential(hass, robot):
    # Established v1 pairing credentials use the same local state schema.
    # No new physical pairing or credential rotation is needed for an upgrade.
    robot.credential, robot.generation, robot.direct_enabled = "dd" * 32, 9, True
    data = {
        "transport": "local",
        "host": robot.host,
        "port": robot.port,
        "robot_id": robot.robot_id,
        "fingerprint": robot.fingerprint,
        "credential": robot.credential,
        "generation": 9,
        "name": robot.name,
        "firmware_version": "13.2.2",
    }
    entry = make_entry(data)
    await hass.config_entries.async_add(entry)
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert dict(entry.data) == data
    await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: entry.runtime_data.client.ready)
    assert dict(entry.data) == data and entry.unique_id == robot.robot_id
    assert not any("/pair/" in path for path, _, _ in robot.http_requests)


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
