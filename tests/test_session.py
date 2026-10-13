"""Proxy-style fake GATT backend through the same HA BLEDevice selection API."""

from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
import pytest
from homeassistant.core import HomeAssistant
from custom_components.flinq_orion.const import PROXY_SERVICE, PROXY_IN, PROXY_OUT
from custom_components.flinq_orion.coordinator import OrionCoordinator
from custom_components.flinq_orion.session import MeshSession
from custom_components.flinq_orion.protocol.proxy import ProxySAR
from custom_components.flinq_orion.protocol.errors import MeshError
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
    }
    hass.config_entries = SimpleNamespace(
        async_update_entry=lambda entry, **kwargs: entry.data.update(kwargs.get("data", {}))
    )
    c = OrionCoordinator(hass, entry)
    s = MeshSession(c)
    c.session = s
    await s.initialize()
    peer = Peer()
    client = Client(peer)
    connector = AsyncMock(side_effect=[ConnectionError("offline"), client])
    with (
        patch(
            "custom_components.flinq_orion.session.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ) as get_device,
        patch("custom_components.flinq_orion.session.establish_connection", connector),
    ):
        try:
            with pytest.raises(ConnectionError):
                await s.ensure_connection()
            await s.ensure_connection()
            assert s.connected
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
            await s.ensure_connection()
            await c.device.query()
            assert s.connected and c.device.dp(1)["value"] is True
        finally:
            await s.stop()
            await c.stop()
    assert all(len(data) <= 20 for data in client.written)
    assert s.rx_worker.done()


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
    c = OrionCoordinator(hass, entry)
    s = MeshSession(c)
    await s.initialize()
    await s.stop()
    await hass.async_add_executor_job(s.store.path.unlink)
    restarted = MeshSession(c)
    with pytest.raises(MeshError):
        await restarted.initialize()
    await restarted.stop()
