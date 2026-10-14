from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from custom_components.flinq_orion.device import OrionDevice
from custom_components.flinq_orion.device_profiles import DeviceCapabilities
from custom_components.flinq_orion.protocol.controller import Response
from custom_components.flinq_orion.protocol.access import AccessMessage


@pytest.mark.asyncio
async def test_sig_temperature_range_and_set_opcodes():
    c = SimpleNamespace(
        entry=SimpleNamespace(options={"transport_mode": "sig"}),
        capabilities=DeviceCapabilities(),
        record=Mock(),
        report_received=Mock(),
    )
    d = OrionDevice(c)
    d.decode_sig(0x8204, b"\x01")
    d.decode_sig(0x824E, bytes.fromhex("0080"))
    d.decode_sig(0x8263, b"\x00" + (2700).to_bytes(2, "little") + (6500).to_bytes(2, "little"))
    d.decode_sig(0x8266, (4000).to_bytes(2, "little") + bytes(2))
    d.decode_sig(0x8278, bytes.fromhex("00800000ffff"))
    assert (
        d.supports_power and d.supports_brightness and d.supports_temperature and d.supports_color
    )
    assert d.temperature_range == (2700, 6500)
    d.ensure_connection = AsyncMock()
    d.next_tid = AsyncMock(return_value=7)
    calls = []

    async def request(payload, matcher, **kwargs):
        calls.append(payload)
        op = payload[:2].hex()
        status, body = {
            "8202": (0x8204, payload[2:3]),
            "824c": (0x824E, payload[2:4]),
            "8264": (0x8266, payload[2:6]),
            "8276": (0x8278, payload[2:8]),
        }[op]
        response = Response(176, 1, 0, 10, False, AccessMessage(status, None, body))
        assert matcher(response)
        assert not matcher(Response(177, 1, 0, 11, False, response.access))
        return response

    d.controller = SimpleNamespace(node=176, request=request)
    await d.set_power(False)
    await d.set_brightness(255)
    await d.set_temperature(4000)
    await d.set_color((0, 100), 255)
    assert calls[0].hex() == "820200070000"
    assert calls[1].hex() == "824cffff070000"
    assert calls[2].hex() == "8264a00f0000070000"
    assert calls[3].hex() == "827600800000ffff070000"


@pytest.mark.asyncio
async def test_discovery_bounds_unsupported_reads_without_marking_device_unresponsive():
    from custom_components.flinq_orion.protocol.errors import MeshTimeoutError

    c = SimpleNamespace(
        entry=SimpleNamespace(options={"transport_mode": "sig", "device_key": "configured"}),
        capabilities=DeviceCapabilities(),
        record=Mock(),
        data={"responsive": True},
        async_set_updated_data=Mock(),
    )
    d = OrionDevice(c)
    d.ensure_connection = AsyncMock()
    request = AsyncMock(side_effect=MeshTimeoutError("Unsupported optional query"))
    d.controller = SimpleNamespace(node=176, request=request)
    results = await d.discover()
    assert len(results) == 7
    for call in request.await_args_list:
        assert call.kwargs["timeout"] == 2
        assert call.kwargs["retries"] == 0
    assert c.data == {"responsive": True}
    c.async_set_updated_data.assert_not_called()
