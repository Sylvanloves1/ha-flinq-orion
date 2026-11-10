"""SIG mode: the colour mode follows confirmed colour and temperature commands.

HSL Set does not change DP 2 (work_mode) on the lamp, so no DP 2 report switches the mode
(seen on a FlinQ Gaia FQC8273). The fake controller answers like the real one: the status
report reaches ``OrionDevice.response`` before the request returns.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from homeassistant.components.light import ColorMode
from homeassistant.exceptions import HomeAssistantError
from custom_components.flinq_orion.device import OrionDevice
from custom_components.flinq_orion.device_profiles import DeviceCapabilities
from custom_components.flinq_orion.light import OrionLight
from custom_components.flinq_orion.protocol.access import AccessMessage
from custom_components.flinq_orion.protocol.controller import Response
from custom_components.flinq_orion.protocol.errors import MeshTimeoutError
from custom_components.flinq_orion.protocol.vendor_tuya import (
    COMPANY_ID,
    DATA,
    DP,
    WRITE,
    encode_dp,
    opcode,
)

STATUS = {"8202": 0x8204, "824c": 0x824E, "8264": 0x8266, "8276": 0x8278}
WHITE_HSL = (65535, 0, 0)


class Coordinator:
    def __init__(self):
        self.entry = SimpleNamespace(options={"transport_mode": "sig", "node_address": 176})
        self.address = "synthetic-device"
        self.capabilities = DeviceCapabilities()
        self.data = {"available": True, "connected": True, "responsive": True}
        self.listeners = []
        self.record = Mock()
        self.updates = 0
        self.device = OrionDevice(self)

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def async_set_updated_data(self, data):
        self.data = data
        self.updates += 1
        for listener in self.listeners:
            listener()

    def report_received(self):
        self.async_set_updated_data(dict(self.data))


def status(opcode, payload, company=None):
    return Response(176, 1, 0, 10, False, AccessMessage(opcode, company, payload))


class Lamp:
    """Answers like the lamp: SIG status echoes the set values, DP writes are reported."""

    def __init__(self, device):
        self.device = device
        self.fail = False
        self.requests = []

    async def request(self, payload, matcher, **kwargs):
        self.requests.append(payload)
        if self.fail:
            raise MeshTimeoutError("No matching response")
        op = payload[:2].hex()
        if op in STATUS:
            body = {"8202": payload[2:3], "824c": payload[2:4], "8264": payload[2:6]}.get(
                op, payload[2:8]
            )
            response = status(STATUS[op], body)
        else:  # vendor write: opcode (3 bytes), then the DP report body
            response = status(DATA, payload[3:], COMPANY_ID)
        await self.device.response(response)
        assert matcher(response)
        return response


def make(temperature_range=False):
    c = Coordinator()
    d = c.device
    d.decode_sig(0x8204, b"\x01")
    d.decode_sig(0x824E, bytes.fromhex("ffff"))
    d.decode_sig(0x8266, (2000).to_bytes(2, "little") + bytes(2))
    d.decode_sig(0x8278, b"".join(n.to_bytes(2, "little") for n in WHITE_HSL))
    if temperature_range:
        d.decode_sig(0x8263, b"\x00" + (2000).to_bytes(2, "little") + (6500).to_bytes(2, "little"))
    d.ensure_connection = AsyncMock()
    d.next_tid = AsyncMock(return_value=7)
    lamp = Lamp(d)
    d.controller = SimpleNamespace(node=176, request=lamp.request)
    light = OrionLight(c)
    seen = []
    c.async_add_listener(lambda: seen.append(light.color_mode))
    return c, light, lamp, seen


@pytest.mark.asyncio
async def test_confirmed_hsl_set_switches_to_hs_without_dp2_report():
    c, light, lamp, seen = make()
    assert light.color_mode == ColorMode.COLOR_TEMP
    await light.async_turn_on(hs_color=(240, 100))
    assert light.color_mode == ColorMode.HS
    assert light.hs_color == pytest.approx((240, 100), abs=0.1)
    assert c.device.state["last_color_mode"] == "hs"
    # The status report notified with the old mode; the last update carries the new one.
    assert seen[-1] == ColorMode.HS


@pytest.mark.asyncio
async def test_failed_hsl_set_keeps_the_mode():
    c, light, lamp, seen = make()
    lamp.fail = True
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(hs_color=(240, 100))
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert "last_color_mode" not in c.device.state


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature_range", [False, True])
async def test_temperature_after_colour_returns_to_color_temp(temperature_range):
    c, light, lamp, seen = make(temperature_range)
    await light.async_turn_on(hs_color=(0, 100))
    assert light.color_mode == ColorMode.HS
    await light.async_turn_on(color_temp_kelvin=2700)
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert seen[-1] == ColorMode.COLOR_TEMP
    # Without the CTL range the DP fallback is used, with it CTL Temperature Set.
    expected = bytes.fromhex("8264") if temperature_range else opcode(WRITE)
    assert lamp.requests[-1].startswith(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature_range", [False, True])
async def test_failed_temperature_set_keeps_hs_mode(temperature_range):
    c, light, lamp, seen = make(temperature_range)
    await light.async_turn_on(hs_color=(0, 100))
    lamp.fail = True
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(color_temp_kelvin=2700)
    assert light.color_mode == ColorMode.HS


@pytest.mark.asyncio
async def test_brightness_only_keeps_hs_mode():
    c, light, lamp, seen = make()
    await light.async_turn_on(hs_color=(120, 100))
    await light.async_turn_on(brightness=100)
    assert light.color_mode == ColorMode.HS
    assert lamp.requests[-1][:2].hex() == "8276"
    assert light.hs_color == pytest.approx((120, 100), abs=0.1)


@pytest.mark.asyncio
async def test_brightness_only_keeps_color_temp_mode():
    c, light, lamp, seen = make()
    await light.async_turn_on(brightness=100)
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert lamp.requests[-1][:2].hex() == "824c"


@pytest.mark.asyncio
async def test_unsolicited_status_reports_do_not_change_the_mode():
    c, light, lamp, seen = make(temperature_range=True)
    await c.device.response(status(0x8278, bytes.fromhex("0080aaaaffff")))
    assert c.device.state["hsl"] == (32768, 43690, 65535)
    assert light.color_mode == ColorMode.COLOR_TEMP
    await light.async_turn_on(hs_color=(0, 100))
    await c.device.response(status(0x8266, (4000).to_bytes(2, "little") + bytes(2)))
    assert c.device.state["kelvin"] == 4000
    assert light.color_mode == ColorMode.HS


@pytest.mark.asyncio
async def test_dp2_reports_still_switch_the_mode():
    c, light, lamp, seen = make()
    report = b"\x01" + encode_dp(DP(2, 4, b"\x01"))
    await c.device.response(status(DATA, report, COMPANY_ID))
    assert light.color_mode == ColorMode.HS
    report = b"\x01" + encode_dp(DP(2, 4, b"\x00"))
    await c.device.response(status(DATA, report, COMPANY_ID))
    assert light.color_mode == ColorMode.COLOR_TEMP


@pytest.mark.asyncio
async def test_hs_mode_survives_persist_and_restore():
    c, light, lamp, seen = make()
    await light.async_turn_on(hs_color=(240, 100))
    restored = Coordinator()
    restored.device.restore(c.device.persist())
    assert restored.device.state["last_color_mode"] == "hs"
    assert OrionLight(restored).color_mode == ColorMode.HS
    assert OrionLight(restored).hs_color == pytest.approx((240, 100), abs=0.1)
