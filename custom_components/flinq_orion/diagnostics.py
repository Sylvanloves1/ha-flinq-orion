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


def network_diagnostics(entry):
    session = getattr(entry.runtime_data, "session", None)
    if session is None:
        return {"sequence": "transmission_disabled"}
    return {
        "connected": session.connected,
        "proxy_lamp": getattr(session.proxy, "title", None),
        "sequence": session.allocator.diagnostics() if session.allocator else None,
        "connection_history": list(session.log.connection_history),
        "packets": list(session.log.records),
    }


def lamp_diagnostics(c):
    return {
        "name": getattr(c, "title", None),
        "model": "FQC8274",
        "pid": "1ajecbtl",
        "node_address": getattr(c, "node_address", None),
        "state": c.data,
        "capabilities": asdict(c.capabilities),
        "device_state": c.device.persist() if getattr(c, "device", None) else {},
        "connection_history": list(c.connection_history),
        "packets": list(c.records),
        "expected_nid": c.codec.nid if c.codec else None,
    }


async def _version(hass):
    # Read from the manifest: a hard-coded value reported 0.2.1 while 0.2.2/0.2.3 ran.
    return str((await async_get_integration(hass, DOMAIN)).version) if hass else "unknown"


async def async_get_config_entry_diagnostics(hass, entry):
    result = {
        "version": await _version(hass),
        "stage": entry.options.get("transport_mode", "observe"),
        "options": dict(entry.options),
        "network": network_diagnostics(entry),
        "lamps": [lamp_diagnostics(lamp.coordinator) for lamp in entry.runtime_data.lamps.values()],
    }
    return async_redact_data(result, REDACT)


async def async_get_device_diagnostics(hass, entry, device):
    lamp = entry.runtime_data.lamp_for_device(device)
    result = {
        "version": await _version(hass),
        "stage": entry.options.get("transport_mode", "observe"),
        "options": dict(entry.options),
        "network": network_diagnostics(entry),
        "lamp": lamp_diagnostics(lamp.coordinator) if lamp else None,
    }
    return async_redact_data(result, REDACT)
