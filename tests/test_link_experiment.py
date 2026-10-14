import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.flinq_orion.session import MeshSession


def session(connected=True):
    s = object.__new__(MeshSession)
    s.client = SimpleNamespace(is_connected=connected, set_connection_params=AsyncMock())
    s.controller = object()
    s.coordinator = SimpleNamespace(
        record=Mock(),
        data={"connected": connected},
        device=SimpleNamespace(discover=AsyncMock(return_value={"power": "response_received"})),
    )
    s.link_test_lock = asyncio.Lock()
    s.link_test_pending = False
    s.ensure_connection = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_connected_experiment_requests_once_and_does_not_persist():
    s = session()
    result = await s.test_connection_parameters()
    s.client.set_connection_params.assert_awaited_once_with(
        min_interval=32, max_interval=32, latency=0, timeout=800
    )
    assert result == {"power": "response_received"}
    assert not s.link_test_pending
    s.ensure_connection.assert_not_awaited()
    records = [call.args[0] for call in s.coordinator.record.call_args_list]
    assert records[-1]["accepted"] == "verify_in_proxy_gap_log"


@pytest.mark.asyncio
async def test_experiment_failure_disarms_request():
    s = session()
    s.client.set_connection_params.side_effect = RuntimeError("API failed")
    with pytest.raises(RuntimeError, match="API failed"):
        await s.test_connection_parameters()
    assert not s.link_test_pending
    s.coordinator.device.discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_in_progress_fallback_applies_exactly_once():
    s = session(False)

    async def ensure():
        # A normal connect already in progress might have passed the experiment hook.
        s.client.is_connected = True
        s.coordinator.data["connected"] = True

    s.ensure_connection.side_effect = ensure
    await s.test_connection_parameters()
    s.client.set_connection_params.assert_awaited_once()
    assert not s.link_test_pending
