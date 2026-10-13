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
