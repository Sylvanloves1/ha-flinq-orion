from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from custom_components.flinq_orion.credential_import import (
    import_credentials,
    matching_entries,
    verify_network_evidence,
    CredentialImportError,
)
from custom_components.flinq_orion.protocol.crypto import k3, k1, s1, aes_block, cmac
from test_options import flow, inputs

ADDRESS = "DC:23:4F:6E:A0:12"
NET = "7dd7364cd842ad18c17c2b820c84c3d6"
APP = "3216d1509884b533248541792b877f98"
UUID = "00001828-0000-1000-8000-00805f9b34fb"


def legacy(**data):
    return SimpleNamespace(
        entry_id="legacy",
        domain="tuya_ble_mesh",
        title="Smart Plug 6E:A0:12",
        data={
            "mac_address": ADDRESS,
            "net_key": NET,
            "app_key": APP,
            "dev_key": "9d6dd0e96eb25dc19a40ed9914f8f03f",
            "unicast_target": "00B0",
            "unicast_our": "0001",
            "iv_index": 0,
            "seq": 100001,
            "mesh_password": "do-not-copy",
            "cloud_token": "do-not-copy",
            **data,
        },
        options={},
    )


def test_import_only_credentials_no_previous_source_or_sequence_reuse():
    original = legacy()
    before = dict(original.data)
    imported, source = import_credentials(original, ADDRESS)
    assert source == 1 and imported["node_address"] == 176
    assert set(imported) == {"net_key", "app_key", "device_key", "node_address", "iv_index"}
    assert original.data == before
    assert "100001" not in str(imported) and "do-not-copy" not in str(imported)


def test_import_matching_mac_and_invalid_keys():
    assert not matching_entries([legacy(mac_address="AA:BB:CC:DD:EE:FF")], ADDRESS)
    with pytest.raises(CredentialImportError) as err:
        import_credentials(legacy(net_key="0" * 32), ADDRESS)
    assert err.value.reason == "import_missing_keys"
    with pytest.raises(CredentialImportError):
        import_credentials(legacy(mac_address="AA:BB:CC:DD:EE:FF"), ADDRESS)


def test_netkey_verified_by_network_identity_and_node_identity():
    imported, _ = import_credentials(legacy(), ADDRESS)
    key = bytes.fromhex(NET)
    assert verify_network_evidence(imported, {UUID: b"\x00" + k3(key)})
    random = bytes.fromhex("0102030405060708")
    identity_key = k1(key, s1(b"nkik"), b"id128\x01")
    hashed = aes_block(identity_key, bytes(6) + random + (176).to_bytes(2, "big"))[8:]
    assert verify_network_evidence(imported, {UUID: b"\x01" + hashed + random})
    with pytest.raises(CredentialImportError) as err:
        verify_network_evidence(imported, {UUID: b"\x00" + bytes(8)})
    assert err.value.reason == "import_network_mismatch"


def test_beacon_authentication_and_iv_mismatch():
    imported, _ = import_credentials(legacy(), ADDRESS)
    key = bytes.fromhex(NET)
    fields = b"\x00" + k3(key) + (1).to_bytes(4, "big")
    auth = cmac(k1(key, s1(b"nkbk"), b"id128\x01"), fields)[:8]
    records = [{"kind": "proxy", "type": 1, "payload": (b"\x01" + fields + auth).hex()}]
    with pytest.raises(CredentialImportError) as err:
        verify_network_evidence(imported, {}, records)
    assert err.value.reason == "import_iv_mismatch"
    imported["iv_index"] = 1
    assert verify_network_evidence(imported, {}, records)
    assert not verify_network_evidence(imported, {})


@pytest.mark.asyncio
async def test_options_import_prefills_password_fields_without_creating_entry():
    f = flow()
    old = legacy()
    f.hass.config_entries.async_entries.return_value = [old]
    f.entry = SimpleNamespace(
        options={},
        entry_id="target",
        data={"address": ADDRESS},
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(records=[], record=Mock())),
    )
    info = SimpleNamespace(service_data={UUID: b"\x00" + k3(bytes.fromhex(NET))})
    with patch(
        "custom_components.flinq_orion.config_flow.bluetooth.async_last_service_info",
        return_value=info,
    ):
        result = await f.async_step_init({"import_entry_id": "legacy"})
    assert result["type"] == "form" and result["step_id"] == "init"
    assert f._import_defaults["transport_mode"] == "query_only"
    assert f._import_defaults["source_address"] == 0
    assert f._import_defaults["net_key"] == NET
    assert f.entry.options == {}
    result = await f.async_step_init(inputs(source_address=1, net_key=NET, app_key=APP))
    assert result["errors"]["source_address"] == "invalid_source"
    result = await f.async_step_init(inputs(net_key=NET, app_key=APP))
    assert result["step_id"] == "profile"
    result = await f.async_step_profile({"hsv_encoding_confirmed": False})
    assert result["data"]["source_address"] == 123
    assert result["data"]["imported_source_address"] == 1
    assert "legacy" not in result["data"].values()
    assert NET not in str(f.entry.runtime_data.coordinator.record.call_args_list)
    assert APP not in str(f.entry.runtime_data.coordinator.record.call_args_list)
