"""FlinQ Orion local Tuya SIG Mesh integration."""

from dataclasses import dataclass, asdict
from typing import Any, cast
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ConfigEntryError
import voluptuous as vol
from .const import DOMAIN
from .coordinator import OrionCoordinator
from .bluetooth import Inspector
from .session import MeshSession

PLATFORMS = ["sensor", "light", "number", "select", "button"]
SERVICES = ("capture", "query_status", "query_dp", "dump_capabilities", "bind_vendor_model")


@dataclass
class RuntimeData:
    coordinator: OrionCoordinator
    inspector: Inspector
    session: MeshSession | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = OrionCoordinator(hass, entry)
    runtime = RuntimeData(coordinator, Inspector(coordinator))
    entry.runtime_data = runtime
    if coordinator.device.mode != "observe":
        try:
            runtime.session = MeshSession(coordinator)
            claims = hass.data.setdefault(f"{DOMAIN}_source_claims", {})
            if runtime.session.identity in claims:
                raise ConfigEntryError(
                    "Controller source address already active in this HA instance"
                )
            claims[runtime.session.identity] = entry.entry_id
            await runtime.session.initialize()
            coordinator.session = runtime.session
        except Exception as err:
            if runtime.session:
                claims = hass.data.get(f"{DOMAIN}_source_claims", {})
                if claims.get(runtime.session.identity) == entry.entry_id:
                    claims.pop(runtime.session.identity, None)
                await runtime.session.stop()
            raise ConfigEntryError(
                f"Mesh setup failed ({type(err).__name__}); verify keys, IV and unique source address"
            ) from err
    await coordinator.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(reload_entry))
    if runtime.session:
        runtime.session.schedule_connect()
    if not hass.services.has_service(DOMAIN, "capture"):

        def selected(call):
            config = hass.config_entries.async_get_entry(call.data["entry_id"])
            if (
                config is None
                or config.domain != DOMAIN
                or getattr(config, "runtime_data", None) is None
            ):
                raise HomeAssistantError("Select a loaded FlinQ Orion config entry")
            return config.runtime_data

        async def action(call: ServiceCall):
            data = selected(call)
            device = data.coordinator.device
            try:
                if call.service == "capture":
                    await (data.session or data.inspector).capture(call.data["seconds"])
                elif call.service == "query_status":
                    return await device.discover(include_optional=call.data["include_optional"])
                elif call.service == "query_dp":
                    await device.query((call.data["dp_id"],))
                elif call.service == "dump_capabilities":
                    return {
                        "capabilities": asdict(data.coordinator.capabilities),
                        "device": device.persist(),
                    }
                elif call.service == "bind_vendor_model":
                    if call.data["confirm"] is not True:
                        raise HomeAssistantError(
                            "Explicit confirmation is required for model binding"
                        )
                    async with device.operation_lock:
                        await device.bind_vendor(call.data["app_key_index"])
            except HomeAssistantError:
                raise
            except Exception as err:
                data.coordinator.record(
                    {"kind": "action_error", "action": call.service, "error": type(err).__name__}
                )
                raise HomeAssistantError(
                    f"{call.service} failed ({type(err).__name__}); download diagnostics"
                ) from err
            return None

        for service in SERVICES:
            fields: dict[Any, Any] = {vol.Required("entry_id"): str}
            response = SupportsResponse.NONE
            if service == "capture":
                fields[vol.Optional("seconds", default=20)] = vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=60)
                )
            elif service == "query_dp":
                fields[vol.Required("dp_id")] = vol.All(vol.Coerce(int), vol.Range(min=1, max=255))
            elif service == "bind_vendor_model":
                fields[vol.Required("confirm")] = bool
                fields[vol.Required("app_key_index")] = vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=4095)
                )
            elif service == "dump_capabilities":
                response = SupportsResponse.ONLY
            elif service == "query_status":
                fields[vol.Optional("include_optional", default=False)] = bool
                response = SupportsResponse.OPTIONAL
            hass.services.async_register(
                DOMAIN,
                service,
                action,
                schema=cast(Any, vol.Schema(fields)),
                supports_response=response,
            )
    return True


async def reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        runtime = entry.runtime_data
        if runtime.session:
            await runtime.session.stop()
            claims = hass.data.get(f"{DOMAIN}_source_claims", {})
            if claims.get(runtime.session.identity) == entry.entry_id:
                claims.pop(runtime.session.identity, None)
        await runtime.inspector.stop()
        await runtime.coordinator.stop()
        if not any(
            e.entry_id != entry.entry_id and getattr(e, "runtime_data", None)
            for e in hass.config_entries.async_entries(DOMAIN)
        ):
            for service in SERVICES:
                hass.services.async_remove(DOMAIN, service)
        return True
    return False
