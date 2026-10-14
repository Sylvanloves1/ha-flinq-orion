"""One link per network (D-015): lamps answer through the lamp that holds the link."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from custom_components.flinq_orion.coordinator import LampEntry, OrionCoordinator
from custom_components.flinq_orion.protocol.errors import MeshTimeoutError
from custom_components.flinq_orion.protocol.vendor_tuya import COMPANY_ID, DATA, DP, query
from custom_components.flinq_orion.session import MeshSession
from test_active_mesh import APP, NET, Peer
from test_session import Client

GARDEN = "DC:23:4F:6E:A0:12"
PORCH = "DC:23:4F:00:00:01"


def peer(address, power):
    lamp = Peer()
    lamp.address = address
    lamp.dps[1] = DP(1, 1, bytes((power,)))
    return lamp


class Mesh:
    """The lamp with the link relays messages to the other lamps it can reach."""

    def __init__(self, proxy, *others):
        self.proxy = proxy
        self.lamps = {lamp.address: lamp for lamp in (proxy, *others)}
        self.unreachable: set[int] = set()

    async def send(self, kind, pdu):
        if kind == 2:
            return await self.proxy.send(kind, pdu)
        destination = self.proxy.codec.decode(pdu, 0).dst
        if destination in self.lamps and destination not in self.unreachable:
            await self.lamps[destination].send(kind, pdu)


class MeshClient(Client):
    async def start_notify(self, uuid, notification):
        self.notification = notification
        for lamp in self.peer.lamps.values():
            lamp.inject = self.inject


def subentry(subentry_id, address, node):
    return ConfigSubentry(
        data=MappingProxyType({"address": address, "node_address": node}),
        subentry_id=subentry_id,
        subentry_type="lamp",
        title=subentry_id.title(),
        unique_id=address,
    )


async def network(tmp_path, *lamps):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="network",
        data={},
        options={
            "transport_mode": "vendor_dp",
            "net_key": NET.hex(),
            "app_key": APP.hex(),
            "source_address": 1,
            "new_source_confirmed": True,
            "iv_index": 0,
        },
        subentries={lamp.subentry_id: lamp for lamp in lamps},
        async_on_unload=Mock(),
    )
    hass.config_entries = SimpleNamespace(
        async_update_entry=lambda entry, **kwargs: entry.data.update(kwargs.get("data", {})),
        async_update_subentry=Mock(),
    )
    session = MeshSession(hass, entry)
    await session.initialize()
    coordinators = []
    for lamp in lamps:
        coordinator = OrionCoordinator(hass, entry, LampEntry(entry, lamp))
        session.attach(coordinator)
        coordinators.append(coordinator)
    return hass, session, coordinators


def links(clients):
    """Each lamp address has its own client; the BLE device is the address itself."""
    connector = AsyncMock(side_effect=lambda cls, device, *args, **kwargs: clients[device])
    return (
        patch(
            "custom_components.flinq_orion.session.bluetooth.async_ble_device_from_address",
            side_effect=lambda hass, address, connectable: address,
        ),
        patch("custom_components.flinq_orion.session.establish_connection", connector),
        connector,
    )


async def stop(session, coordinators):
    await session.stop()
    for coordinator in coordinators:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_second_lamp_answers_through_the_existing_link(tmp_path):
    _, s, (garden, porch) = await network(
        tmp_path, subentry("garden", GARDEN, 176), subentry("porch", PORCH, 177)
    )
    mesh = Mesh(peer(176, 0), peer(177, 1))
    device_patch, connect_patch, connector = links({GARDEN: MeshClient(mesh)})
    try:
        with device_patch, connect_patch:
            await garden.device.query()
            await porch.device.query()
        assert connector.await_count == 1 and s.proxy is garden
        assert garden.device.dp(1)["value"] is False
        assert porch.device.dp(1)["value"] is True
        assert porch.data["connected"] and porch.device.controller is s.controller
    finally:
        await stop(s, (garden, porch))


@pytest.mark.asyncio
async def test_unknown_mesh_address_is_detected_and_stored(tmp_path):
    hass, s, (porch,) = await network(tmp_path, subentry("porch", PORCH, None))
    assert porch.node_address is None
    device_patch, connect_patch, _ = links({PORCH: MeshClient(Mesh(peer(177, 1)))})
    try:
        with device_patch, connect_patch:
            await porch.device.query()
        assert porch.node_address == 177 and porch.device.dp(1)["value"] is True
        update = hass.config_entries.async_update_subentry.call_args
        assert update.args[1].subentry_id == "porch"
        assert update.kwargs["data"] == {"address": PORCH, "node_address": 177}
        assert any(r["kind"] == "node_address" for r in porch.connection_history)
    finally:
        await stop(s, (porch,))


@pytest.mark.asyncio
async def test_lamp_without_a_known_address_is_never_reached_through_another_lamp(tmp_path):
    _, s, (garden, porch) = await network(
        tmp_path, subentry("garden", GARDEN, 176), subentry("porch", PORCH, None)
    )
    clients = {GARDEN: MeshClient(Mesh(peer(176, 0))), PORCH: MeshClient(Mesh(peer(177, 1)))}
    device_patch, connect_patch, connector = links(clients)
    try:
        with device_patch, connect_patch:
            await garden.device.query()
            await porch.device.query()
        assert connector.await_count == 2 and s.proxy is porch
        assert porch.node_address == 177
    finally:
        await stop(s, (garden, porch))


@pytest.mark.asyncio
async def test_lamp_that_does_not_answer_through_the_proxy_is_connected_directly(tmp_path):
    _, s, (garden, porch) = await network(
        tmp_path, subentry("garden", GARDEN, 176), subentry("porch", PORCH, 177)
    )
    relay = Mesh(peer(176, 0), peer(177, 1))
    relay.unreachable.add(177)
    clients = {GARDEN: MeshClient(relay), PORCH: MeshClient(Mesh(peer(177, 1)))}
    device_patch, connect_patch, connector = links(clients)
    try:
        with device_patch, connect_patch:
            await garden.device.query()
            response = await porch.device._request(
                query((0,)), DATA, COMPANY_ID, model=4, timeout=0.2, discovery=True
            )
        assert response.source == 177 and s.proxy is porch
        assert connector.await_count == 2
        assert any(r["kind"] == "relay_fallback" for r in porch.connection_history)
        with device_patch, connect_patch:
            clients[PORCH].peer.unreachable.add(177)
            with pytest.raises(MeshTimeoutError):
                # The lamp already holds the link: no second direct attempt.
                await porch.device._request(
                    query((0,)), DATA, COMPANY_ID, model=4, timeout=0.2, discovery=True
                )
        assert connector.await_count == 2
    finally:
        await stop(s, (garden, porch))


@pytest.mark.asyncio
async def test_persistent_link_reads_each_lamp_once(tmp_path):
    _, s, (garden, porch) = await network(
        tmp_path, subentry("garden", GARDEN, 176), subentry("porch", PORCH, 177)
    )
    s.on_demand = False
    device_patch, connect_patch, connector = links(
        {GARDEN: MeshClient(Mesh(peer(176, 0), peer(177, 1)))}
    )
    try:
        with device_patch, connect_patch:
            s.schedule_connect(garden)
            await s.tasks[GARDEN]
            s.schedule_connect(porch)
            await s.tasks[PORCH]
            s.schedule_connect(porch)
            assert s.tasks[PORCH].done()
        assert connector.await_count == 1
        assert porch.device.dp(1)["value"] is True
        assert set(s.last_refresh) == {GARDEN, PORCH}
    finally:
        await stop(s, (garden, porch))
