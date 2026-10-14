"""On-demand connections for the battery lamp (D-009, D-010), with a synthetic peer."""

import asyncio
from unittest.mock import AsyncMock
import pytest
from bleak_retry_connector import MAX_CONNECT_ATTEMPTS
from custom_components.flinq_orion import session as session_module
from custom_components.flinq_orion.entity import OrionEntity
from custom_components.flinq_orion.protocol.errors import DeviceUnavailableError
from test_active_mesh import Peer
from test_entities import Coordinator
from test_session import Client, patched_connection, started_session


def kinds(c):
    return [record["kind"] for record in c.connection_history]


@pytest.fixture
def short_idle(monkeypatch):
    monkeypatch.setattr(session_module, "IDLE_DISCONNECT_SECONDS", 0.05)


@pytest.mark.asyncio
async def test_unused_link_closes_after_idle_time(tmp_path, short_idle):
    c, s = await started_session(tmp_path)
    client = Client(Peer())
    device_patch, connect_patch = patched_connection(AsyncMock(return_value=client))
    try:
        with device_patch, connect_patch as connector:
            await s.ensure_connection()
            assert s.connected
            assert connector.call_args.kwargs["max_attempts"] == 1
            await asyncio.sleep(0.2)
        assert not s.connected and not client.is_connected
        assert c.data["connected"] is False and c.device.controller is None
        closed = [r for r in c.connection_history if r["kind"] == "idle_disconnect"]
        assert len(closed) == 1 and closed[0]["link_lifetime_s"] >= 0
        assert "disconnected" not in kinds(c)
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_requests_and_running_operations_keep_the_link_open(tmp_path, short_idle):
    c, s = await started_session(tmp_path)
    device_patch, connect_patch = patched_connection(AsyncMock(return_value=Client(Peer())))
    try:
        with device_patch, connect_patch:
            for _ in range(5):
                await c.device.query()
                await asyncio.sleep(0.03)
            assert s.connected
            async with c.device.operation_lock:
                await asyncio.sleep(0.2)
                assert s.connected
            await asyncio.sleep(0.2)
        assert not s.connected
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_refresh_runs_only_when_due_and_backs_off_after_failure(tmp_path, short_idle):
    c, s = await started_session(tmp_path)
    connector = AsyncMock(return_value=Client(Peer()))
    device_patch, connect_patch = patched_connection(connector)
    try:
        with device_patch, connect_patch:
            s.schedule_connect()
            await s.task
            assert "refresh" in kinds(c) and s.last_refresh is not None
            await asyncio.sleep(0.2)
            assert not s.connected
            s.schedule_connect()
            assert s.task.done() and connector.await_count == 1
    finally:
        await s.stop()
        await c.stop()

    c, s = await started_session(tmp_path)
    device_patch, connect_patch = patched_connection(AsyncMock(side_effect=OSError("offline")))
    try:
        with device_patch, connect_patch:
            s.schedule_connect()
            await s.task
            failed = s.task
            s.schedule_connect()
        assert s.task is failed and s.failures == 1 and s.last_refresh is None
        assert c.connection_history[-1]["error"] == "DeviceUnavailableError"
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_on_demand_connect_gives_up_at_the_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "ON_DEMAND_CONNECT_DEADLINE", 0.05)
    c, s = await started_session(tmp_path)

    async def never_connects(*args, **kwargs):
        await asyncio.sleep(5)

    device_patch, connect_patch = patched_connection(AsyncMock(side_effect=never_connects))
    try:
        with device_patch, connect_patch:
            with pytest.raises(DeviceUnavailableError, match="battery") as unreachable:
                await s.ensure_connection()
        assert isinstance(unreachable.value.__cause__, TimeoutError)
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.asyncio
async def test_persistent_mode_keeps_the_link_and_reconnects(tmp_path, short_idle):
    c, s = await started_session(tmp_path, connection_mode="persistent")
    connector = AsyncMock(return_value=Client(Peer()))
    device_patch, connect_patch = patched_connection(connector)
    try:
        with device_patch, connect_patch:
            s.schedule_connect()
            await s.task
            assert connector.call_args.kwargs["max_attempts"] == MAX_CONNECT_ATTEMPTS
            await asyncio.sleep(0.2)
            assert s.connected and "idle_disconnect" not in kinds(c)
    finally:
        await s.stop()
        await c.stop()


@pytest.mark.parametrize(
    ("mode", "data", "available"),
    [
        ("on_demand", {"available": True, "connected": False, "responsive": False}, True),
        ("on_demand", {"available": False, "connected": False, "responsive": True}, False),
        ("persistent", {"available": True, "connected": False, "responsive": True}, False),
        ("persistent", {"available": True, "connected": True, "responsive": True}, True),
    ],
)
def test_entity_availability_follows_the_connection_mode(mode, data, available):
    coordinator = Coordinator({"connection_mode": mode})
    coordinator.data = data
    assert OrionEntity(coordinator, "power").available is available


def test_existing_entries_default_to_on_demand():
    coordinator = Coordinator()
    coordinator.data = {"available": True, "connected": False, "responsive": False}
    assert OrionEntity(coordinator, "power").available is True
