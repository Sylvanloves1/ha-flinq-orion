"""HA 2026.10 classes with isolated Bluetooth/storage boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_UNKNOWN
from custom_components.flinq_orion.config_flow import ConfigFlow
from custom_components.flinq_orion.const import TARGET_ADDRESS, DOMAIN
from custom_components.flinq_orion.sensor import async_setup_entry
from custom_components.flinq_orion.bluetooth import Inspector
from custom_components.flinq_orion.protocol.errors import DeviceUnavailableError
from custom_components.flinq_orion.coordinator import OrionCoordinator


@pytest.mark.asyncio
async def test_config_flow_form_and_create(tmp_path):
    flow = ConfigFlow()
    flow.hass = HomeAssistant(str(tmp_path))
    flow.context = {"source": "user"}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    form = await flow.async_step_user()
    assert form["type"] == "form"
    invalid = await flow.async_step_user({"address": "bad"})
    assert invalid["errors"]["address"] == "invalid_address"
    result = await flow.async_step_user({"address": TARGET_ADDRESS})
    assert result["type"] == "create_entry"
    assert result["data"]["address"] == TARGET_ADDRESS
    flow.async_set_unique_id.assert_awaited_once_with(TARGET_ADDRESS)


@pytest.mark.asyncio
async def test_discovery_rejects_other_mesh(tmp_path):
    flow = ConfigFlow()
    flow.hass = HomeAssistant(str(tmp_path))
    flow.context = {"source": "bluetooth"}
    result = await flow.async_step_bluetooth(SimpleNamespace(address="AA:BB:CC:DD:EE:FF"))
    assert result["reason"] == "not_orion"


@pytest.mark.asyncio
async def test_entity_creation_availability(tmp_path):
    c = Mock()
    c.address = TARGET_ADDRESS
    c.data = {"rssi": -38, "available": True}
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=c))
    added = []
    await async_setup_entry(HomeAssistant(str(tmp_path)), entry, added.extend)
    assert len(added) == 1
    entity = added[0]
    assert entity.unique_id == TARGET_ADDRESS + "_rssi"
    assert entity.device_info["identifiers"] == {(DOMAIN, TARGET_ADDRESS)}
    assert entity.native_value == -38 and entity.available
    c.data["available"] = False
    assert not entity.available


@pytest.mark.asyncio
async def test_capture_retries_on_next_invocation_and_disconnects():
    c = Mock()
    c.address = TARGET_ADDRESS
    c.data = {}
    inspector = Inspector(c)
    client = Mock()
    client.services = []
    client.disconnect = AsyncMock()

    # Service collection protocol with iterable and lookups.
    class Services:
        def __iter__(self):
            return iter([])

        def get_service(self, uuid):
            return None

    client.services = Services()
    connector = AsyncMock(side_effect=[ConnectionError("offline"), client])
    with (
        patch(
            "custom_components.flinq_orion.bluetooth.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch("custom_components.flinq_orion.bluetooth.establish_connection", connector),
    ):
        with pytest.raises(ConnectionError):
            await inspector.capture()
        await inspector.capture()
    client.disconnect.assert_awaited_once()
    assert inspector.client is None
    assert c.data["gatt"] == []


@pytest.mark.asyncio
async def test_unreachable_never_connects():
    c = Mock()
    inspector = Inspector(c)
    with patch(
        "custom_components.flinq_orion.bluetooth.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        with pytest.raises(DeviceUnavailableError):
            await inspector.capture()


@pytest.mark.asyncio
async def test_coordinator_unknown_and_malformed(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = Mock()
    entry.data = {"address": TARGET_ADDRESS}
    entry.options = {}
    entry.entry_id = "test"
    c = OrionCoordinator(hass, entry)
    assert c.data["rssi"] is None
    assert STATE_UNKNOWN == "unknown"
    c.network_received(b"")
    assert c.records[-1]["packet_nid"] is None
    assert len(c.records) <= 100


@pytest.mark.asyncio
async def test_diagnostics_redacts_keys(tmp_path):
    from custom_components.flinq_orion.diagnostics import async_get_config_entry_diagnostics

    c = Mock()
    c.data = {"source": "private-proxy-address"}
    c.capabilities = __import__(
        "custom_components.flinq_orion.device_profiles", fromlist=["DeviceCapabilities"]
    ).DeviceCapabilities()
    c.records = []
    c.codec = None
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=c),
        options={"net_key": "secret-netkey", "app_key": "secret-appkey", "iv_index": 0},
    )
    result = await async_get_config_entry_diagnostics(None, entry)
    assert "secret-netkey" not in str(result)
    assert "secret-appkey" not in str(result)
    assert "private-proxy-address" not in str(result)
    assert result["options"]["iv_index"] == 0
