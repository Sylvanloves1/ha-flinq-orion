"""Schema settings (D-011) as entities, and the derived battery warning."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from custom_components.flinq_orion.binary_sensor import OrionBatteryLow
from custom_components.flinq_orion.coordinator import OrionCoordinator
from custom_components.flinq_orion.device_profiles import ORION_DPS
from custom_components.flinq_orion.number import OrionSettingNumber
from custom_components.flinq_orion.protocol.advertisements import PROXY
from custom_components.flinq_orion.protocol.errors import TuyaDPError
from custom_components.flinq_orion.protocol.vendor_tuya import DP
from custom_components.flinq_orion.select import OrionSettingSelect
from custom_components.flinq_orion.sensor import OrionPirState
from custom_components.flinq_orion.switch import OrionSettingSwitch
from test_entities import Coordinator, report


def value(identifier, number):
    return DP(identifier, 2, number.to_bytes(4, "big"))


def enum(identifier, index):
    return DP(identifier, 4, bytes((index,)))


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ((2, "scene"), DP(2, 4, b"\x02")),
        ((53, "5lux"), DP(53, 4, b"\x04")),
        ((55, 3600), value(55, 3600)),
        ((56, True), DP(56, 1, b"\x01")),
        ((6, "captured"), DP(6, 3, b"captured")),
    ],
)
def test_schema_encoding(given, expected):
    identifier, raw = given
    assert ORION_DPS[identifier].encode(raw) == expected


@pytest.mark.parametrize(
    ("identifier", "raw"),
    [(2, "disco"), (55, 4), (55, 3601), (58, "5"), (59, True)],
)
def test_schema_encoding_rejects_values_outside_the_schema(identifier, raw):
    with pytest.raises(TuyaDPError):
        ORION_DPS[identifier].encode(raw)


@pytest.mark.asyncio
async def test_settings_entities_read_reports_and_write_through_the_schema():
    c = Coordinator()
    await report(
        c.device,
        value(7, 300),
        enum(2, 1),
        enum(51, 0),
        enum(52, 0),
        enum(53, 2),
        enum(54, 1),
        value(55, 60),
        DP(56, 1, b"\x01"),
        value(58, 5),
        value(59, 250),
    )
    mode, control, cds = (OrionSettingSelect(c, i) for i in (2, 51, 53))
    assert mode.options == ["white", "colour", "scene"] and mode.current_option == "colour"
    assert control.current_option == "auto" and cds.current_option == "50lux"
    countdown, standby = OrionSettingNumber(c, 7), OrionSettingNumber(c, 59)
    assert countdown.unique_id.endswith("_countdown") and countdown.native_value == 300
    assert countdown.native_max_value == 86400
    assert standby.native_value == 25 and standby.native_max_value == 100
    switch, pir = OrionSettingSwitch(c, 56), OrionPirState(c)
    assert switch.is_on is True and pir.native_value == "pir"

    c.device.write_setting = AsyncMock()
    await mode.async_select_option("scene")
    await standby.async_set_native_value(30)
    await switch.async_turn_off()
    assert [call.args for call in c.device.write_setting.await_args_list] == [
        (2, "scene"),
        (59, 300),
        (56, False),
    ]
    c.device.write_setting = AsyncMock(side_effect=TuyaDPError("outside the schema"))
    with pytest.raises(HomeAssistantError):
        await control.async_select_option("manual")


@pytest.mark.asyncio
async def test_unreported_or_mistyped_settings_stay_unknown():
    c = Coordinator()
    await report(c.device, value(54, 1))
    assert OrionSettingSelect(c, 54).current_option is None
    assert OrionSettingNumber(c, 55).native_value is None
    assert OrionSettingSwitch(c, 56).is_on is None


def advertisement(service_data):
    return SimpleNamespace(
        rssi=-47,
        source="proxy",
        service_uuids=[PROXY],
        service_data={PROXY: service_data},
        manufacturer_data={},
        raw=None,
    )


def node_identity(random):
    return b"\x01" + bytes(8) + random


@pytest.mark.asyncio
async def test_battery_warning_follows_the_low_power_advertising_cycle(tmp_path):
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {"address": "DC:23:4F:6E:A0:12"}
    entry.options = {}
    c = OrionCoordinator(HomeAssistant(str(tmp_path)), entry)
    sensor = OrionBatteryLow(c)
    assert sensor.is_on is None and sensor.available
    c.advertisement(advertisement(node_identity(b"\x01" * 8)), None)
    c.advertisement(advertisement(node_identity(b"\x02" * 8)), None)
    assert sensor.is_on is False
    c.advertisement(advertisement(node_identity(b"\x03" * 8)), None)
    assert sensor.is_on is True
    c.advertisement(advertisement(b"\x00" + bytes(8)), None)
    assert sensor.is_on is False
    states = [r["battery_low_suspected"] for r in c.connection_history]
    assert states == [False, True, False]
