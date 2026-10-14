from dataclasses import asdict
from homeassistant.components.diagnostics import async_redact_data

REDACT = {
    "address",
    "net_key",
    "app_key",
    "device_key",
    "device_uuid",
    "source",
    "unique_id",
    "entry_id",
    "identifier",
    "identifiers",
}


async def async_get_config_entry_diagnostics(hass, entry):
    c = entry.runtime_data.coordinator
    result = {
        "version": "0.2.4",
        "stage": getattr(getattr(c, "device", None), "mode", "observe"),
        "model": "FQC8274",
        "pid": "1ajecbtl",
        "reported_unicast": "0x00B0",
        "reported_company_id": "0x07D0",
        "reported_vendor_model": "0x0004",
        "reported_firmware": "5.1",
        "options": dict(entry.options),
        "state": c.data,
        "capabilities": asdict(c.capabilities),
        "device_state": c.device.persist() if getattr(c, "device", None) else {},
        "packets": list(c.records),
        "sequence": entry.runtime_data.session.allocator.diagnostics()
        if getattr(entry.runtime_data, "session", None) and entry.runtime_data.session.allocator
        else "transmission_disabled",
        "expected_nid": c.codec.nid if c.codec else None,
    }
    return async_redact_data(result, REDACT)
