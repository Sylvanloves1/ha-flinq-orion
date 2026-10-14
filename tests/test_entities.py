from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from homeassistant.components.light import ColorMode
from custom_components.flinq_orion.device import OrionDevice
from custom_components.flinq_orion.device_profiles import DeviceCapabilities
from custom_components.flinq_orion.protocol.controller import Response
from custom_components.flinq_orion.protocol.access import AccessMessage
from custom_components.flinq_orion.protocol.vendor_tuya import DP, encode_dp
from custom_components.flinq_orion.protocol.errors import MeshTimeoutError
from custom_components.flinq_orion.light import async_setup_entry, OrionLight
from custom_components.flinq_orion.number import OrionCountdown
from custom_components.flinq_orion.select import OrionMode
from homeassistant.exceptions import HomeAssistantError


class Coordinator:
    def __init__(self, options=None):
        self.entry = SimpleNamespace(options={"transport_mode": "vendor_dp", **(options or {})})
        self.address = "synthetic-device"
        self.capabilities = DeviceCapabilities()
        self.data = {"available": True, "connected": True, "responsive": True}
        self.listeners = []
        self.record = Mock()
        self.device = OrionDevice(self)

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def report_received(self):
        for listener in self.listeners:
            listener()


async def report(device, *dps):
    await device.response(
        Response(
            176,
            1,
            0,
            5,
            False,
            AccessMessage(0xCD, 0x7D0, b"\x01" + b"".join(encode_dp(dp) for dp in dps)),
        )
    )


@pytest.mark.asyncio
async def test_light_created_after_observation_once_and_unknowns_stay_diagnostics():
    c = Coordinator()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=c), async_on_unload=Mock())
    entities = []
    await async_setup_entry(None, entry, entities.extend)
    assert not entities
    await report(c.device, DP(54, 2, (30).to_bytes(4, "big")))
    assert not entities
    await report(c.device, DP(1, 1, b"\x00"))
    assert len(entities) == 1
    light = entities[0]
    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.is_on is False
    await report(c.device, DP(3, 2, (750).to_bytes(4, "big")))
    assert len(entities) == 1
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert light.brightness == 191


@pytest.mark.asyncio
async def test_light_no_optimistic_update_on_failed_write():
    c = Coordinator()
    await report(c.device, DP(1, 1, b"\x00"))
    light = OrionLight(c)
    c.device.set_power = AsyncMock(side_effect=MeshTimeoutError("no response"))
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert light.is_on is False


@pytest.mark.asyncio
async def test_color_and_ct_require_explicit_metadata():
    c = Coordinator()
    await report(
        c.device, DP(1, 1, b"\x01"), DP(4, 2, (500).to_bytes(4, "big")), DP(5, 3, b"007803e803e8")
    )
    light = OrionLight(c)
    assert light.supported_color_modes == {ColorMode.ONOFF}
    c.entry.options.update(
        hsv_encoding_confirmed=True, ct_range_confirmed=True, min_kelvin=2700, max_kelvin=6500
    )
    assert light.supported_color_modes == {ColorMode.HS}
    await report(c.device, DP(3, 2, (500).to_bytes(4, "big")))
    assert light.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert light.color_temp_kelvin == 4600
    assert light.hs_color == (120, 100)


@pytest.mark.asyncio
async def test_countdown_and_modes_from_observed_data():
    c = Coordinator({"countdown_seconds_confirmed": True, "countdown_max_seconds": 3600})
    await report(c.device, DP(7, 2, (300).to_bytes(4, "big")), DP(2, 4, b"\x03"))
    number = OrionCountdown(c)
    assert number.native_max_value == 3600 and number.native_value == 300
    selector = OrionMode(c)
    assert selector.options == ["3"] and selector.current_option == "3"
    with pytest.raises(HomeAssistantError):
        await selector.async_select_option("0")


@pytest.mark.asyncio
async def test_no_entities_for_query_only():
    c = Coordinator({"transport_mode": "query_only"})
    await report(c.device, DP(1, 1, b"\x01"), DP(3, 2, (500).to_bytes(4, "big")))
    assert not c.device.supports_power and not c.device.supports_brightness


@pytest.mark.asyncio
async def test_unknown_scenes_cannot_be_written():
    c = Coordinator({"scene_replay_confirmed": True})
    await report(c.device, DP(6, 0, b"captured"))
    assert list(c.device.scenes) == ["Scene 1"]
    from custom_components.flinq_orion.protocol.errors import MeshError

    with pytest.raises(MeshError):
        await c.device.write_dp(DP(6, 0, b"never-observed"))


@pytest.mark.asyncio
async def test_brightness_preserves_color_mode():
    c = Coordinator({"hsv_encoding_confirmed": True, "color_mode_value": 1})
    await report(
        c.device,
        DP(1, 1, b"\x01"),
        DP(2, 4, b"\x01"),
        DP(3, 2, (750).to_bytes(4, "big")),
        DP(5, 3, b"007803e801f4"),
    )
    light = OrionLight(c)
    assert light.color_mode == ColorMode.HS and light.brightness == 128
    c.device.set_power = AsyncMock()
    c.device.select_light_mode = AsyncMock()
    c.device.set_color = AsyncMock()
    c.device.set_brightness = AsyncMock()
    await light.async_turn_on(brightness=100)
    c.device.set_color.assert_awaited_once_with((120, 100), 100)
    c.device.set_brightness.assert_not_awaited()
    c.device.set_power.assert_not_awaited()
    await light.async_turn_on()
    c.device.set_power.assert_awaited_once_with(True)
