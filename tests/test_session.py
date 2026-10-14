"""Proxy-style fake GATT backend through the same HA BLEDevice selection API."""

from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
import pytest
from homeassistant.core import HomeAssistant
from custom_components.flinq_orion.const import PROXY_SERVICE, PROXY_IN, PROXY_OUT
from custom_components.flinq_orion.coordinator import OrionCoordinator
from custom_components.flinq_orion.session import MeshSession
from custom_components.flinq_orion.protocol.proxy import ProxySAR
from custom_components.flinq_orion.protocol.errors import DeviceUnavailableError, MeshError
from test_active_mesh import Peer, NET, APP


class Services:
    def __iter__(self):
        return iter(
            [
                SimpleNamespace(
                    uuid=PROXY_SERVICE,
                    characteristics=[
                        SimpleNamespace(uuid=PROXY_IN, properties=["write-without-response"]),
                        SimpleNamespace(uuid=PROXY_OUT, properties=["notify"]),
                    ],
                )
            ]
        )

    def get_service(self, uuid):
        return object() if uuid == PROXY_SERVICE else None

    def get_characteristic(self, uuid):
        return object() if uuid in (PROXY_IN, PROXY_OUT) else None


class Client:
    def __init__(self, peer):
        self.peer = peer
        self.services = Services()
        self.is_connected = True
        self.notification = None
        self.written = []
        self.sar = ProxySAR()

    async def start_notify(self, uuid, notification):
        assert uuid == PROXY_OUT
        self.notification = notification
        self.peer.inject = self.inject

    def inject(self, kind, pdu):
        pieces = [pdu[i : i + 19] for i in range(0, len(pdu), 19)]
        for n, piece in enumerate(pieces):
            sar = 0 if len(pieces) == 1 else 1 if n == 0 else 3 if n == len(pieces) - 1 else 2
            self.notification(1, bytes(((sar << 6) | kind,)) + piece)

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == PROXY_IN and response is False and len(data) <= 20
        self.written.append(data)
        result = self.sar.feed(data)
        if result:
            await self.peer.send(*result)

    async def disconnect(self):
        self.is_connected = False


@pytest.mark.asyncio
async def test_active_gatt_connect_reports_and_reconnect(tmp_path):
    c, s = await started_session(tmp_path)
    hass = c.hass
    peer = Peer()
    client = Client(peer)
    bad_client = Client(peer)
    bad_client.start_notify = AsyncMock(side_effect=MeshError("notification setup failed"))
    bad_client.clear_cache = AsyncMock(return_value=True)
    connector = AsyncMock(side_effect=[ConnectionError("offline"), bad_client, client])
    with (
        patch(
            "custom_components.flinq_orion.session.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ) as get_device,
        patch("custom_components.flinq_orion.session.establish_connection", connector),
    ):
        try:
            with pytest.raises(DeviceUnavailableError, match="battery") as unreachable:
                await s.ensure_connection(c)
            assert isinstance(unreachable.value.__cause__, ConnectionError)
            with pytest.raises(MeshError, match="notification setup failed"):
                await s.ensure_connection(c)
            # A setup failure must not force the next connection onto the uncached path.
            bad_client.clear_cache.assert_not_awaited()
            assert not bad_client.is_connected
            assert not s.connected
            assert any(
                record.get("stage") == "subscribe_proxy_notifications"
                and record["kind"] == "connection_error"
                and record["link_lifetime_s"] >= 0
                for record in s.log.connection_history
            )
            await s.ensure_connection(c)
            assert s.connected
            assert "use_services_cache" not in connector.call_args.kwargs
            await c.device.query()
            assert c.device.supports_power
            await c.device.set_power(True)
            assert c.device.dp(1)["value"] is True
            get_device.assert_called_with(hass, c.address, connectable=True)
            s._disconnected(client)
            assert not s.connected and c.device.controller is None
            client.is_connected = False
            fresh = Client(peer)
            connector.side_effect = None
            connector.return_value = fresh
            await s.ensure_connection(c)
            await c.device.query()
            assert s.connected and c.device.dp(1)["value"] is True
        finally:
            await s.stop()
            await c.stop()
    assert all(len(data) <= 20 for data in client.written)
    assert s.rx_worker.done()


async def started_session(tmp_path, **options):
    """A network entry that also describes its one lamp, as before 0.4.0."""
    hass = HomeAssistant(str(tmp_path))
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {"address": "DC:23:4F:6E:A0:12"}
    entry.options = {
        "transport_mode": "vendor_dp",
        "net_key": NET.hex(),
        "app_key": APP.hex(),
        "source_address": 1,
        "node_address": 176,
        "new_source_confirmed": True,
        "iv_index": 0,
        **options,
    }
    hass.config_entries = SimpleNamespace(
        async_update_entry=lambda entry, **kwargs: entry.data.update(kwargs.get("data", {}))
    )
    c = OrionCoordinator(hass, entry)
    s = MeshSession(hass, entry)
    await s.initialize()
    s.attach(c)
    return c, s


def patched_connection(connector):
    return (
        patch(
            "custom_components.flinq_orion.session.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch("custom_components.flinq_orion.session.establish_connection", connector),
    )


class NoProxyServices(Services):
    def get_service(self, uuid):
        return None


@pytest.mark.asyncio
async def test_only_a_table_without_proxy_service_clears_the_gatt_cache(tmp_path):
    c, s = await started_session(tmp_path)
    stale = Client(Peer())
    stale.services = NoProxyServices()
    stale.clear_cache = AsyncMock(return_value=True)
    device_patch, connect_patch = patched_connection(AsyncMock(return_value=stale))
    try:
        with device_patch, connect_patch:
            with pytest.raises(MeshError, match="0x1828"):
                await s.ensure_connection(c)
        stale.clear_cache.assert_awaited_once()
        assert {"kind": "gatt_cache_clear", "cleared": True}.items() <= s.log.records[-1].items()
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_link_lifetime_survives_packet_records_and_probe_measures_each_read(tmp_path):
    c, s = await started_session(tmp_path)
    client = Client(Peer())
    device_patch, connect_patch = patched_connection(AsyncMock(return_value=client))
    try:
        with device_patch, connect_patch:
            summary = await s.probe_link(c, seconds=0.05, interval=0.01)
            assert summary["probes"] >= 2
            assert summary["responses"] == summary["probes"]
            assert summary["links_used"] == 1 and summary["longest_link_s"] >= 0
            for n in range(150):
                s.record({"kind": "proxy", "n": n})
            s._disconnected(client)
        kinds = [record["kind"] for record in s.log.connection_history]
        assert kinds[:3] == ["connect_attempt", "link_open", "connection_setup"]
        assert "connection_ready" in kinds
        assert "link_probe" in [record["kind"] for record in c.connection_history]
        disconnected = s.log.connection_history[-1]
        assert disconnected["kind"] == "disconnected"
        assert disconnected["stage"] == "ready" and disconnected["link_lifetime_s"] >= 0
        assert all(record["kind"] in ("proxy", "disconnected") for record in s.log.records)
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_probe_records_failures_and_continues(tmp_path):
    c, s = await started_session(tmp_path)
    device_patch, connect_patch = patched_connection(
        AsyncMock(side_effect=ConnectionError("offline"))
    )
    try:
        with device_patch, connect_patch:
            summary = await s.probe_link(c, seconds=0.03, interval=0.01)
        assert summary["responses"] == 0
        assert summary["failures"] == {"DeviceUnavailableError": summary["probes"]}
        assert summary["links_used"] == 0 and summary["probes"] >= 2
        assert c.data["command_failures"] == 0
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_missing_sequence_store_after_initialization_blocks_restart(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {"address": "device"}
    entry.options = {
        "transport_mode": "query_only",
        "net_key": NET.hex(),
        "app_key": APP.hex(),
        "source_address": 1,
        "new_source_confirmed": True,
        "iv_index": 0,
    }
    hass.config_entries = SimpleNamespace(
        async_update_entry=lambda entry, **kwargs: entry.data.update(kwargs.get("data", {}))
    )
    s = MeshSession(hass, entry)
    await s.initialize()
    await s.stop()
    await hass.async_add_executor_job(s.store.path.unlink)
    restarted = MeshSession(hass, entry)
    with pytest.raises(MeshError):
        await restarted.initialize()
    await restarted.stop()


def test_connection_error_preserves_nested_cause_and_redacts_keys_and_addresses():
    from bleak_retry_connector import BleakOutOfConnectionSlotsError

    session = object.__new__(MeshSession)
    session.entry = SimpleNamespace(options={"net_key": NET.hex()})
    session.lamps = []
    cause = TimeoutError("Timeout waiting for connection slot on DC:23:4F:6E:A0:12")
    error = BleakOutOfConnectionSlotsError(f"Failed on DC:23:4F:6E:A0:12 {NET.hex()}")
    error.__cause__ = cause
    result = session.connection_error(error)
    assert result["error"] == "BleakOutOfConnectionSlotsError"
    assert result["causes"][1]["message"].startswith("Timeout waiting for connection slot")
    assert NET.hex() not in str(result)
    assert "DC:23:4F:6E:A0:12" not in str(result)
