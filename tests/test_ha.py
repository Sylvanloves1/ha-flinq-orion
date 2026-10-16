"""HA 2026.10 classes with isolated Bluetooth/storage boundaries."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from custom_components.flinq_orion import (
    Lamp,
    RuntimeData,
    async_migrate_entry,
    config_snapshot,
    register_services,
    reload_entry,
)
from custom_components.flinq_orion.config_flow import ConfigFlow, LampFlow
from custom_components.flinq_orion.const import DOMAIN, NETWORK_TITLE, PROXY_SERVICE
from custom_components.flinq_orion.sensor import async_setup_entry
from custom_components.flinq_orion.bluetooth import Inspector
from custom_components.flinq_orion.protocol.crypto import k3
from custom_components.flinq_orion.protocol.errors import DeviceUnavailableError
from custom_components.flinq_orion.coordinator import OrionCoordinator

ADDRESS = "DC:23:4F:6E:A0:12"
SECOND = "DC:23:4F:00:00:01"
NET = "7dd7364cd842ad18c17c2b820c84c3d6"
APP = "3216d1509884b533248541792b877f98"
NETWORK_ID = b"\x00" + k3(bytes.fromhex(NET))


def lamp_subentry(address=ADDRESS, node_address=176, subentry_id="lamp", **data):
    return ConfigSubentry(
        data=MappingProxyType({"address": address, "node_address": node_address, **data}),
        subentry_id=subentry_id,
        subentry_type="lamp",
        title="Garden",
        unique_id=address,
    )


def network(*subentries, **options):
    return SimpleNamespace(
        entry_id="network",
        title="Home mesh",
        data={},
        options={"net_key": NET, "app_key": APP, "source_address": 2, **options},
        subentries={subentry.subentry_id: subentry for subentry in subentries},
        update_listeners=[],
    )


def config_hass(networks=(), providers=()):
    def entries(domain=None, *args, **kwargs):
        return list(providers) if domain == "tuya_ble_mesh" else list(networks)

    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=Mock(side_effect=entries),
            async_add_subentry=Mock(),
            async_update_subentry=Mock(return_value=True),
            async_schedule_reload=Mock(),
            async_reload=AsyncMock(),
        )
    )


def config_flow(hass, source="user"):
    flow = ConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": source}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    return flow


def advertising(service_data):
    return patch(
        "custom_components.flinq_orion.config_flow.bluetooth.async_last_service_info",
        return_value=SimpleNamespace(service_data=service_data) if service_data else None,
    )


@pytest.mark.asyncio
async def test_config_flow_creates_a_network_with_its_first_lamp():
    flow = config_flow(config_hass())
    menu = await flow.async_step_user()
    assert menu["type"] == "menu" and menu["menu_options"] == ["pair_network", "manual"]
    form = await flow.async_step_manual()
    assert form["type"] == "form" and form["step_id"] == "manual"
    invalid = await flow.async_step_manual({"address": "bad", "name": "Garden"})
    assert invalid["errors"]["address"] == "invalid_address"
    with advertising(None):
        result = await flow.async_step_manual({"address": ADDRESS.lower(), "name": "Garden"})
    assert result["type"] == "create_entry" and result["version"] == 2
    assert result["title"] == NETWORK_TITLE and result["data"] == {}
    assert list(result["subentries"]) == [
        {
            "data": {"address": ADDRESS, "node_address": None},
            "subentry_type": "lamp",
            "title": "Garden",
            "unique_id": ADDRESS,
        }
    ]
    flow.async_set_unique_id.assert_awaited_once_with(ADDRESS)


@pytest.mark.asyncio
async def test_a_lamp_of_a_configured_network_joins_that_network():
    hass = config_hass([network(lamp_subentry())])
    flow = config_flow(hass)
    with advertising({PROXY_SERVICE: NETWORK_ID}):
        result = await flow.async_step_manual({"address": SECOND, "name": "Porch"})
    assert result["type"] == "abort" and result["reason"] == "lamp_added"
    added = hass.config_entries.async_add_subentry.call_args.args[1]
    assert added.title == "Porch" and added.unique_id == SECOND
    assert dict(added.data) == {"address": SECOND, "node_address": None}
    result = await config_flow(hass).async_step_manual({"address": ADDRESS, "name": "Again"})
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_discovery_offers_only_identified_lamps():
    home = network(lamp_subentry())

    def discover(hass, address=SECOND, service_data=None):
        info = SimpleNamespace(address=address, service_data=service_data or {})
        return config_flow(hass, "bluetooth").async_step_bluetooth(info)

    result = await discover(config_hass([home]), ADDRESS, {PROXY_SERVICE: NETWORK_ID})
    assert result["reason"] == "already_configured"
    hass = config_hass([home])
    flow = config_flow(hass, "bluetooth")
    info = SimpleNamespace(address=SECOND, service_data={PROXY_SERVICE: NETWORK_ID})
    result = await flow.async_step_bluetooth(info)
    assert result["type"] == "form" and result["step_id"] == "add_lamp"
    assert result["description_placeholders"]["network"] == "Home mesh"
    result = await flow.async_step_add_lamp({"name": "Porch"})
    assert result["reason"] == "lamp_added"
    hass.config_entries.async_add_subentry.assert_called_once()
    other = {PROXY_SERVICE: b"\x00" + bytes(8)}
    assert (await discover(config_hass([home]), service_data=other))["reason"] == "not_in_network"
    assert (await discover(config_hass(), service_data=other))["reason"] == "not_identified"
    provider = SimpleNamespace(
        entry_id="tuya",
        domain="tuya_ble_mesh",
        title="Tuya lamp",
        data={"mac_address": SECOND, "net_key": NET},
        options={},
    )
    result = await discover(config_hass(providers=[provider]), service_data=other)
    assert result["type"] == "form" and result["step_id"] == "manual"


def lamp_flow(hass, home, source="user", subentry=None):
    flow = LampFlow()
    flow.hass = hass
    flow.handler = ("network", "lamp")
    flow.context = {"source": source}
    flow._get_entry = Mock(return_value=home)
    flow._get_reconfigure_subentry = Mock(return_value=subentry)
    return flow


@pytest.mark.asyncio
async def test_add_lamp_lists_lamps_of_this_network_first():
    home = network(lamp_subentry())
    hass = config_hass([home])
    nearby = [
        SimpleNamespace(
            address="AA:00:00:00:00:01", rssi=-80, service_data={PROXY_SERVICE: b"\x00" + bytes(8)}
        ),
        SimpleNamespace(address=SECOND.lower(), rssi=-60, service_data={PROXY_SERVICE: NETWORK_ID}),
        SimpleNamespace(address=ADDRESS, rssi=-50, service_data={PROXY_SERVICE: NETWORK_ID}),
        SimpleNamespace(address="AA:00:00:00:00:02", rssi=-70, service_data={}),
    ]
    flow = lamp_flow(hass, home)
    with patch(
        "custom_components.flinq_orion.config_flow.bluetooth.async_discovered_service_info",
        return_value=nearby,
    ):
        menu = await flow.async_step_user()
        assert menu["menu_options"] == ["existing"]  # no running session: no pairing
        form = await flow.async_step_existing()
        selector = form["data_schema"].schema["address"]
        options = [option["value"] for option in selector.config["options"]]
        assert options == [SECOND, "AA:00:00:00:00:01"]
        result = await flow.async_step_existing({"address": ADDRESS, "name": "Again"})
        assert result["errors"]["address"] == "lamp_configured"
    result = await flow.async_step_existing({"address": SECOND, "name": "Porch"})
    assert result["type"] == "create_entry" and result["unique_id"] == SECOND
    assert result["data"] == {"address": SECOND, "node_address": None}


@pytest.mark.asyncio
async def test_lamp_settings_change_name_address_and_device_key():
    subentry = lamp_subentry()
    home = network(subentry)
    hass = config_hass([home])
    flow = lamp_flow(hass, home, "reconfigure", subentry)
    form = await flow.async_step_reconfigure()
    assert form["step_id"] == "settings"
    result = await flow.async_step_reconfigure({"name": "Porch", "node_address": 2})
    assert result["errors"]["node_address"] == "invalid_node"
    result = await flow.async_step_reconfigure(
        {"name": "Porch", "node_address": 176, "device_key": "00" * 16}
    )
    assert result["errors"]["device_key"] == "invalid_key"
    result = await flow.async_step_reconfigure(
        {"name": "Porch", "node_address": 0, "device_key": "AB" * 16}
    )
    assert result["reason"] == "reconfigure_successful"
    update = hass.config_entries.async_update_subentry.call_args.kwargs
    assert update["title"] == "Porch"
    assert update["data"] == {"address": ADDRESS, "node_address": None, "device_key": "ab" * 16}
    hass.config_entries.async_schedule_reload.assert_called_once_with("network")


@pytest.mark.asyncio
async def test_migration_moves_the_lamp_into_a_subentry():
    entry = SimpleNamespace(
        version=1,
        entry_id="network",
        title="FlinQ Smart Outdoor Lamp Orion",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "sequence_scope": "scope:0002"},
        options={
            "transport_mode": "sig",
            "net_key": NET,
            "app_key": APP,
            "device_key": "9d" * 16,
            "source_address": 2,
            "node_address": 176,
        },
    )
    hass = config_hass()
    hass.config_entries.async_update_entry = Mock()
    hass.config_entries.async_entry_for_domain_unique_id = Mock(return_value=None)
    registries = Mock()
    devices, entities = registries.devices, registries.entities
    lamp_device = SimpleNamespace(id="lamp-device", identifiers={(DOMAIN, ADDRESS)})
    other_device = SimpleNamespace(id="other-device", identifiers={(DOMAIN, SECOND)})
    lamp_entities = [
        SimpleNamespace(
            entity_id="light.garden", unique_id=f"{ADDRESS}_light", config_subentry_id=None
        ),
        SimpleNamespace(
            entity_id="sensor.other", unique_id=f"{SECOND}_rssi", config_subentry_id=None
        ),
    ]
    with (
        patch("custom_components.flinq_orion.dr.async_get", return_value=devices),
        patch(
            "custom_components.flinq_orion.dr.async_entries_for_config_entry",
            return_value=[lamp_device, other_device],
        ),
        patch("custom_components.flinq_orion.er.async_get", return_value=entities),
        patch(
            "custom_components.flinq_orion.er.async_entries_for_config_entry",
            return_value=lamp_entities,
        ),
    ):
        assert await async_migrate_entry(hass, entry)
    update = hass.config_entries.async_update_entry.call_args.kwargs
    assert update["version"] == 2 and update["unique_id"] == k3(bytes.fromhex(NET)).hex()
    assert update["data"] == {"sequence_scope": "scope:0002"}
    assert update["options"] == {
        "transport_mode": "sig",
        "net_key": NET,
        "app_key": APP,
        "source_address": 2,
    }
    lamp = hass.config_entries.async_add_subentry.call_args.args[1]
    assert lamp.title == entry.title and lamp.unique_id == ADDRESS
    assert dict(lamp.data) == {
        "address": ADDRESS,
        "node_address": 176,
        "device_key": "9d" * 16,
        "migrated": True,
    }
    # The device and its entities move explicitly; an implicit move is deprecated.
    devices.async_update_device.assert_called_once_with(
        "lamp-device", new_config_subentry_id=lamp.subentry_id
    )
    entities.async_update_entity.assert_called_once_with(
        "light.garden", config_subentry_id=lamp.subentry_id
    )
    # Entities first: HA removes entities left in the old subentry when the device moves.
    assert [call[0] for call in registries.mock_calls] == [
        "entities.async_update_entity",
        "devices.async_update_device",
    ]


@pytest.mark.asyncio
async def test_migration_without_keys_leaves_the_address_to_detection():
    entry = SimpleNamespace(
        version=1, title="Orion", unique_id=ADDRESS, data={"address": ADDRESS}, options={}
    )
    hass = config_hass()
    hass.config_entries.async_update_entry = Mock()
    with patch("custom_components.flinq_orion.move_lamp_to_subentry") as move:
        assert await async_migrate_entry(hass, entry)
    move.assert_called_once()
    assert hass.config_entries.async_update_entry.call_args.kwargs["unique_id"] == ADDRESS
    lamp = hass.config_entries.async_add_subentry.call_args.args[1]
    assert lamp.data["node_address"] is None
    entry.version = 3
    assert not await async_migrate_entry(hass, entry)


@pytest.mark.asyncio
async def test_a_detected_mesh_address_does_not_reload_the_network():
    home = network(lamp_subentry(node_address=None))
    home.runtime_data = SimpleNamespace(config=config_snapshot(home))
    hass = config_hass([home])
    home.subentries = {"lamp": lamp_subentry(node_address=177)}
    await reload_entry(hass, home)
    hass.config_entries.async_reload.assert_not_awaited()
    home.subentries = {"lamp": lamp_subentry(node_address=177, device_key="ab" * 16)}
    await reload_entry(hass, home)
    hass.config_entries.async_reload.assert_awaited_once_with("network")


@pytest.mark.asyncio
async def test_actions_select_the_lamp_by_its_device():
    c = Mock()
    c.address = ADDRESS
    c.capabilities = __import__(
        "custom_components.flinq_orion.device_profiles", fromlist=["DeviceCapabilities"]
    ).DeviceCapabilities()
    c.device.persist.return_value = {"state": {}}
    runtime = RuntimeData({"lamp": Lamp(c, Mock())})
    hass = SimpleNamespace(
        services=Mock(),
        config_entries=SimpleNamespace(
            async_loaded_entries=Mock(return_value=[SimpleNamespace(runtime_data=runtime)])
        ),
    )
    devices = {
        "lamp-device": SimpleNamespace(identifiers={(DOMAIN, ADDRESS)}),
        "other-device": SimpleNamespace(identifiers={("other", ADDRESS)}),
    }
    registry = SimpleNamespace(async_get=devices.get)
    with patch("custom_components.flinq_orion.dr.async_get", return_value=registry):
        register_services(hass)
        calls = {call.args[1]: call.args[2] for call in hass.services.async_register.call_args_list}
        action = calls["dump_capabilities"]
        result = await action(
            SimpleNamespace(service="dump_capabilities", data={"device_id": "lamp-device"})
        )
        assert result["device"] == {"state": {}}
        with pytest.raises(HomeAssistantError, match="lamp"):
            await action(
                SimpleNamespace(service="dump_capabilities", data={"device_id": "other-device"})
            )


@pytest.mark.asyncio
async def test_entity_creation_availability(tmp_path):
    c = Mock()
    c.address = ADDRESS
    c.title = "Garden"
    c.data = {"rssi": -38, "available": True}
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(lamps={"lamp": SimpleNamespace(coordinator=c)}),
        async_on_unload=Mock(),
    )
    added = []
    await async_setup_entry(
        HomeAssistant(str(tmp_path)),
        entry,
        lambda new, config_subentry_id: added.extend((e, config_subentry_id) for e in new),
    )
    assert len(added) == 1
    entity, subentry_id = added[0]
    assert subentry_id == "lamp"
    assert entity.unique_id == ADDRESS + "_rssi"
    assert entity.device_info["identifiers"] == {(DOMAIN, ADDRESS)}
    assert entity.device_info["name"] == "Garden"
    assert entity.native_value == -38 and entity.available
    c.data["available"] = False
    assert not entity.available


@pytest.mark.asyncio
async def test_capture_retries_on_next_invocation_and_disconnects():
    c = Mock()
    c.address = ADDRESS
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
    entry.data = {"address": ADDRESS}
    entry.options = {}
    entry.entry_id = "test"
    c = OrionCoordinator(hass, entry)
    assert c.data["rssi"] is None
    assert STATE_UNKNOWN == "unknown"
    c.network_received(b"")
    assert c.records[-1]["packet_nid"] is None
    assert len(c.records) <= 100


def diagnostics_coordinator(**data):
    c = Mock()
    c.title, c.node_address = "Garden", 176
    c.data = data
    c.capabilities = __import__(
        "custom_components.flinq_orion.device_profiles", fromlist=["DeviceCapabilities"]
    ).DeviceCapabilities()
    c.records = []
    c.connection_history = [{"kind": "disconnected", "link_lifetime_s": 3.1}]
    c.codec = None
    c.address = ADDRESS
    return c


@pytest.mark.asyncio
async def test_diagnostics_redacts_keys(tmp_path):
    from custom_components.flinq_orion.diagnostics import (
        async_get_config_entry_diagnostics,
        async_get_device_diagnostics,
    )

    c = diagnostics_coordinator(source="private-proxy-address")
    c.records = [{"kind": "tx_access", "uses_device_key": True}]
    entry = SimpleNamespace(
        runtime_data=RuntimeData({"lamp": Lamp(c, Mock())}),
        options={"net_key": "secret-netkey", "app_key": "secret-appkey", "iv_index": 0},
    )
    result = await async_get_config_entry_diagnostics(None, entry)
    assert "secret-netkey" not in str(result)
    assert "secret-appkey" not in str(result)
    assert "private-proxy-address" not in str(result)
    assert result["options"]["iv_index"] == 0
    assert result["network"] == {"sequence": "transmission_disabled"}
    lamp = result["lamps"][0]
    assert lamp["name"] == "Garden" and lamp["node_address"] == 176
    assert lamp["connection_history"][0]["link_lifetime_s"] == 3.1
    assert lamp["packets"][0]["uses_device_key"] is True
    device = SimpleNamespace(identifiers={(DOMAIN, ADDRESS)})
    result = await async_get_device_diagnostics(None, entry, device)
    assert result["lamp"]["name"] == "Garden" and "secret-netkey" not in str(result)


@pytest.mark.asyncio
async def test_diagnostics_version_comes_from_manifest(tmp_path):
    import json
    from pathlib import Path
    from custom_components.flinq_orion.diagnostics import async_get_config_entry_diagnostics

    manifest = Path(__file__).parents[1] / "custom_components/flinq_orion/manifest.json"
    entry = SimpleNamespace(
        runtime_data=RuntimeData({"lamp": Lamp(diagnostics_coordinator(), Mock())}), options={}
    )
    with patch(
        "custom_components.flinq_orion.diagnostics.async_get_integration",
        AsyncMock(
            return_value=SimpleNamespace(version=json.loads(manifest.read_text())["version"])
        ),
    ):
        result = await async_get_config_entry_diagnostics(object(), entry)
    assert result["version"] == json.loads(manifest.read_text())["version"]
