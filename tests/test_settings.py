"""Schema encoding (D-011), the motion sensor switch, the battery status and the
removal of entities that are no longer provided (D-013)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from custom_components.flinq_orion import remove_retired_entities
from custom_components.flinq_orion.binary_sensor import OrionBatteryStatus
from custom_components.flinq_orion.coordinator import OrionCoordinator
from custom_components.flinq_orion.device_profiles import ORION_DPS
from custom_components.flinq_orion.protocol.advertisements import PROXY
from custom_components.flinq_orion.protocol.errors import TuyaDPError
from custom_components.flinq_orion.protocol.vendor_tuya import DP
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
async def test_motion_sensor_switch_reads_reports_and_writes_through_the_schema():
    c = Coordinator()
    switch = OrionSettingSwitch(c, 56)
    assert switch.is_on is None
    await report(c.device, value(56, 1))  # wrong type for a boolean: stays unknown
    assert switch.is_on is None
    await report(c.device, DP(56, 1, b"\x01"))
    assert switch.is_on is True
    c.device.write_setting = AsyncMock()
    await switch.async_turn_off()
    await switch.async_turn_on()
    assert [call.args for call in c.device.write_setting.await_args_list] == [
        (56, False),
        (56, True),
    ]
    c.device.write_setting = AsyncMock(side_effect=TuyaDPError("outside the schema"))
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_off()


def test_retired_entities_are_removed_from_the_registry():
    registry = Mock()
    entries = [
        SimpleNamespace(unique_id="lamp_cds", entity_id="select.threshold"),
        SimpleNamespace(unique_id="lamp_rssi", entity_id="sensor.signal"),
        SimpleNamespace(unique_id="lamp_switch_pir", entity_id="switch.motion"),
        SimpleNamespace(unique_id="lamp_battery_low", entity_id="binary_sensor.old_battery"),
        SimpleNamespace(unique_id="other_cds", entity_id="select.other_lamp"),
    ]
    with (
        patch("custom_components.flinq_orion.er.async_get", return_value=registry),
        patch(
            "custom_components.flinq_orion.er.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        remove_retired_entities(None, SimpleNamespace(entry_id="entry"), "lamp")
    removed = [call.args for call in registry.async_remove.call_args_list]
    assert removed == [("select.threshold",), ("binary_sensor.old_battery",)]


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
async def test_battery_status_follows_the_low_power_advertising_cycle(tmp_path):
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {"address": "DC:23:4F:6E:A0:12"}
    entry.options = {}
    c = OrionCoordinator(HomeAssistant(str(tmp_path)), entry)
    sensor = OrionBatteryStatus(c)
    assert sensor.unique_id.endswith("_battery")
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
