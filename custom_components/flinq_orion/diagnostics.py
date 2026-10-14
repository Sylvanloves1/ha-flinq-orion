from dataclasses import asdict
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.loader import async_get_integration
from .const import DOMAIN

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
    # Read from the manifest: a hard-coded value reported 0.2.1 while 0.2.2/0.2.3 ran.
    version = str((await async_get_integration(hass, DOMAIN)).version) if hass else "unknown"
    result = {
        "version": version,
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
        "connection_history": list(c.connection_history),
        "packets": list(c.records),
        "sequence": entry.runtime_data.session.allocator.diagnostics()
        if getattr(entry.runtime_data, "session", None) and entry.runtime_data.session.allocator
        else "transmission_disabled",
        "expected_nid": c.codec.nid if c.codec else None,
    }
    return async_redact_data(result, REDACT)
