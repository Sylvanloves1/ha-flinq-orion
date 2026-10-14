"""Regression fixture from physical user capture, with identity fields omitted."""

import json
from pathlib import Path
from custom_components.flinq_orion.const import (
    PROXY_SERVICE,
    PROXY_IN,
    PROXY_OUT,
    PROVISIONING_SERVICE,
)


def test_physical_capture_has_standard_proxy_bearer():
    capture = json.loads((Path(__file__).parent / "fixtures/orion_gatt.json").read_text())
    services = {item["uuid"]: item for item in capture["services"]}
    assert PROXY_SERVICE in services
    assert PROVISIONING_SERVICE not in services
    chars = {
        item["uuid"]: item["properties"] for item in services[PROXY_SERVICE]["characteristics"]
    }
    assert "write-without-response" in chars[PROXY_IN]
    assert "notify" in chars[PROXY_OUT]
    assert capture["capture_states"].count("complete") == 2
    assert capture["dp_reports_received"] is False


def test_physical_sig_status_values_do_not_use_the_unscaled_sig_kelvin():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from custom_components.flinq_orion.device import OrionDevice
    from custom_components.flinq_orion.device_profiles import DeviceCapabilities
    from custom_components.flinq_orion.light import OrionLight
    from homeassistant.components.light import ColorMode

    capture = json.loads((Path(__file__).parent / "fixtures/orion_sig_status.json").read_text())
    coordinator = Mock()
    coordinator.entry = SimpleNamespace(options={"transport_mode": "sig"})
    coordinator.address = "synthetic-test-address"
    coordinator.capabilities = DeviceCapabilities()
    coordinator.device = OrionDevice(coordinator)
    for report in capture["reports"]:
        coordinator.device.decode_sig(report["opcode"], bytes.fromhex(report["payload"]))
    assert coordinator.device.state["power"] is True
    assert coordinator.device.state["brightness"] == 39321
    assert coordinator.device.state["kelvin"] == 8480
    assert coordinator.device.state["hsl"] == (19660, 0, 65535)
    assert coordinator.device.supports_brightness and coordinator.device.supports_color
    # The lamp never answered CTL Range Get, so 8480 K is not used. Colour temperature
    # goes through DP 4 with the default range (D-011) and is unknown until DP 4 reports.
    assert coordinator.device.supports_temperature
    assert coordinator.device.temperature_range == (2000, 6500)
    light = OrionLight(coordinator)
    assert light.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert light.color_temp_kelvin is None
    assert light.brightness == 153
    assert light.hs_color == (0, 100)
