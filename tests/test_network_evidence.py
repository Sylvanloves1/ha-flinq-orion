"""Passive network membership from proxy advertising, with synthetic packets only."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from homeassistant.core import HomeAssistant
from custom_components.flinq_orion.coordinator import OrionCoordinator
from custom_components.flinq_orion.protocol.advertisements import (
    PROVISIONING,
    PROXY,
    identity_material,
    network_evidence,
    node_identity_hash,
    raw_service_data,
)
from custom_components.flinq_orion.protocol.crypto import aes_block, k1, k3, s1

# Published Mesh Profile sample NetKey, also used by the other protocol tests.
NET = bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6")
RANDOM = bytes.fromhex("0102030405060708")


def node_identity(address=176, key=NET):
    _, identity_key = identity_material(key)
    return b"\x01" + node_identity_hash(identity_key, RANDOM, address) + RANDOM


def packet(*structures):
    return b"".join(bytes((len(body) + 1, ad_type)) + body for ad_type, body in structures)


def test_identity_material_matches_specification_derivation():
    network_id, identity_key = identity_material(NET)
    assert network_id.hex() == "3ecaff672f673370"
    assert identity_key == k1(NET, s1(b"nkik"), b"id128\x01")
    expected = aes_block(identity_key, bytes(6) + RANDOM + (176).to_bytes(2, "big"))[8:]
    assert node_identity_hash(identity_key, RANDOM, 176) == expected


@pytest.mark.parametrize(
    ("proxy_data", "result"),
    [
        (b"\x00" + k3(NET), "network_id_match"),
        (b"\x00" + bytes(8), "mismatch"),
        (node_identity(), "node_identity_match"),
        (node_identity(address=177), "mismatch"),
        (node_identity(key=bytes(16)), "mismatch"),
        (b"\x02" + bytes(16), "private_identity_unverified"),
        (b"\x03" + bytes(16), "private_identity_unverified"),
        (b"\x01" + bytes(3), "unknown_proxy_identity"),
        (None, "no_proxy_identity"),
    ],
)
def test_network_evidence(proxy_data, result):
    network_id, identity_key = identity_material(NET)
    assert network_evidence(proxy_data, network_id, identity_key, 176) == result


def test_raw_service_data_reads_only_the_latest_packet_and_stops_on_a_bad_length():
    proxy = node_identity()
    raw = packet((0x01, b"\x06"), (0x03, b"\x28\x18"), (0x16, b"\x28\x18" + proxy))
    assert raw_service_data(raw) == {PROXY: proxy}
    provisioning = bytes(16) + b"\x00\x00"
    raw = packet((0x16, b"\x27\x18" + provisioning)) + b"\x1f\x16\x28"
    assert raw_service_data(raw) == {PROVISIONING: provisioning}
    assert raw_service_data(b"") == {}


def info(raw, service_data):
    return SimpleNamespace(
        rssi=-45,
        source="proxy",
        service_uuids=list(service_data),
        service_data=service_data,
        manufacturer_data={},
        raw=raw,
    )


@pytest.mark.asyncio
async def test_coordinator_ignores_merged_old_provisioning_data_and_warns_once(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {"address": "DC:23:4F:6E:A0:12"}
    entry.options = {"net_key": NET.hex(), "node_address": 176}
    c = OrionCoordinator(hass, entry)
    proxy = node_identity()
    provisioning = bytes(16) + b"\x00\x00"
    merged = {PROXY: proxy, PROVISIONING: provisioning}

    c.advertisement(info(packet((0x16, b"\x28\x18" + proxy)), merged), None)
    assert c.data["network_evidence"]["result"] == "node_identity_match"
    assert c.data["network_evidence"]["unprovisioned_packet"] is False
    assert not [r for r in c.connection_history if r["kind"] == "lamp_state_warning"]

    latest = packet((0x16, b"\x27\x18" + provisioning))
    c.advertisement(info(latest, merged), None)
    c.advertisement(info(latest, merged), None)
    warnings = [r for r in c.connection_history if r["kind"] == "lamp_state_warning"]
    assert len(warnings) == 1 and warnings[0]["unprovisioned_packet"] is True
    assert c.data["network_evidence_counts"] == {
        "node_identity_match": 3,
        "unprovisioned_packet": 2,
    }
    assert NET.hex() not in str(c.data) and NET.hex() not in str(list(c.records))


@pytest.mark.asyncio
async def test_coordinator_without_raw_packet_reports_unknown_provisioning_state(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {"address": "DC:23:4F:6E:A0:12"}
    entry.options = {"net_key": NET.hex()}
    c = OrionCoordinator(hass, entry)
    c.advertisement(info(None, {PROXY: b"\x00" + bytes(8)}), None)
    assert c.data["network_evidence"]["result"] == "mismatch"
    assert c.data["network_evidence"]["unprovisioned_packet"] is None
    assert [
        r["network_evidence"] for r in c.connection_history if r["kind"] == "lamp_state_warning"
    ] == ["mismatch"]
