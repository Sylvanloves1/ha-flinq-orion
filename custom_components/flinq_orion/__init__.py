"""FlinQ Orion local Tuya SIG Mesh integration.

A config entry is one mesh network; each lamp is a config subentry (D-014, D-015).
"""

from dataclasses import dataclass, asdict, field
import logging
from types import MappingProxyType
from typing import Any, cast
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ConfigEntryError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import voluptuous as vol
from .const import DOMAIN, LEGACY_NODE_ADDRESS, SUBENTRY_LAMP
from .coordinator import LampEntry, OrionCoordinator
from .bluetooth import Inspector
from .protocol.advertisements import identity_material
from .pairing import PairingStore
from .session import MeshSession

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor", "light", "switch", "button"]
# Entities that earlier versions created and that are no longer provided (D-013).
RETIRED_ENTITY_KEYS = (
    "operating_mode",
    "work_mode",
    "device_mode",
    "cds",
    "pir_sensitivity",
    "countdown",
    "pir_delay",
    "standby_time",
    "standby_bright",
    "pir_state",
    "battery_low",
)
SERVICES = (
    "capture",
    "query_status",
    "query_dp",
    "dump_capabilities",
    "bind_vendor_model",
    "probe_link",
)


def remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
    """Remove registry entries of entities this version no longer provides."""
    registry = er.async_get(hass)
    retired = {f"{address}_{key}" for key in RETIRED_ENTITY_KEYS}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id in retired:
            registry.async_remove(entity.entity_id)


def sync_configuration_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A repair issue per paired lamp whose configuration did not finish (D-022)."""
    wanted = {}
    for subentry in entry.subentries.values():
        issue_id = f"configuration_{subentry.subentry_id}"
        if subentry.data.get("configured", True) is False:
            wanted[issue_id] = subentry.title
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="configuration_incomplete",
                translation_placeholders={"name": subentry.title},
            )
    for subentry_id in entry.subentries:
        issue_id = f"configuration_{subentry_id}"
        if issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def log_interrupted_pairings(hass: HomeAssistant, session: MeshSession) -> None:
    store = PairingStore(hass, session.key_scope)
    await store.load()
    known = {lamp.address for lamp in session.lamps}
    for record in store.pending:
        if record["address"] not in known:
            _LOGGER.warning(
                "A lamp was paired at mesh address %s but has no lamp entry; "
                "restore it with Add lamp, Restore an interrupted pairing",
                record["unicast"],
            )


def network_unique_id(options) -> str | None:
    """Network ID of the NetKey: one config entry per mesh network."""
    net_key = options.get("net_key")
    return identity_material(bytes.fromhex(net_key))[0].hex() if net_key else None


def config_snapshot(entry: ConfigEntry):
    """Configuration that requires a reload. A detected mesh address does not."""
    return (
        dict(entry.options),
        {key: value for key, value in entry.data.items() if key != "sequence_scope"},
        {
            subentry_id: (
                subentry.title,
                {key: value for key, value in subentry.data.items() if key != "node_address"},
            )
            for subentry_id, subentry in entry.subentries.items()
        },
    )


@dataclass
class Lamp:
    coordinator: OrionCoordinator
    inspector: Inspector


@dataclass
class RuntimeData:
    lamps: dict[str, Lamp]
    session: MeshSession | None = None
    config: Any = field(default=None)

    def lamp_for_device(self, device) -> Lamp | None:
        for lamp in self.lamps.values():
            if (DOMAIN, lamp.coordinator.address) in device.identifiers:
                return lamp
        return None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version 1 held one lamp; version 2 is a network with one subentry per lamp.

    Options, the controller address and the entity unique IDs stay the same, so the
    sequence store, the replay store and the entity history continue.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        options = dict(entry.options)
        address = entry.data["address"]
        node = options.pop("node_address", None)
        if node is None and options.get("net_key"):
            node = LEGACY_NODE_ADDRESS
        lamp: dict[str, Any] = {"address": address, "node_address": node, "migrated": True}
        if device_key := options.pop("device_key", ""):
            lamp["device_key"] = device_key
        unique_id = network_unique_id(options)
        if unique_id is None or hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, unique_id
        ):
            unique_id = entry.unique_id
        hass.config_entries.async_update_entry(
            entry,
            data={key: value for key, value in entry.data.items() if key != "address"},
            options=options,
            unique_id=unique_id,
            version=2,
        )
        subentry = ConfigSubentry(
            data=MappingProxyType(lamp),
            subentry_type=SUBENTRY_LAMP,
            title=entry.title,
            unique_id=address,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        move_lamp_to_subentry(hass, entry, address, subentry.subentry_id)
    return True


def move_lamp_to_subentry(hass, entry, address, subentry_id) -> None:
    """Move the lamp entities, then the lamp device, to the subentry.

    A device belongs to one subentry since HA 2026.10; an implicit move is deprecated.
    The order matters: when a device moves, HA removes the entities that are still in
    its old subentry.
    """
    entities = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        if entity.unique_id.startswith(f"{address}_") and (
            entity.config_subentry_id != subentry_id
        ):
            entities.async_update_entity(entity.entity_id, config_subentry_id=subentry_id)
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if (DOMAIN, address) in device.identifiers:
            devices.async_update_device(device.id, new_config_subentry_id=subentry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    lamps = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_LAMP:
            coordinator = OrionCoordinator(hass, entry, LampEntry(entry, subentry))
            lamps[subentry.subentry_id] = Lamp(coordinator, Inspector(coordinator))
    runtime = RuntimeData(lamps, config=config_snapshot(entry))
    entry.runtime_data = runtime
    if entry.options.get("transport_mode", "observe") != "observe":
        try:
            runtime.session = MeshSession(hass, entry)
            claims = hass.data.setdefault(f"{DOMAIN}_source_claims", {})
            if runtime.session.identity in claims:
                raise ConfigEntryError(
                    "Controller source address already active in this HA instance"
                )
            claims[runtime.session.identity] = entry.entry_id
            await runtime.session.initialize()
            for lamp in lamps.values():
                runtime.session.attach(lamp.coordinator)
        except Exception as err:
            if runtime.session:
                claims = hass.data.get(f"{DOMAIN}_source_claims", {})
                if claims.get(runtime.session.identity) == entry.entry_id:
                    claims.pop(runtime.session.identity, None)
                await runtime.session.stop()
            raise ConfigEntryError(
                f"Mesh setup failed ({type(err).__name__}); verify keys, IV and unique source address"
            ) from err
    unique_id = network_unique_id(entry.options)
    if (
        unique_id
        and entry.unique_id != unique_id
        and not hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id)
    ):
        hass.config_entries.async_update_entry(entry, unique_id=unique_id)
    for lamp in lamps.values():
        await lamp.coordinator.start()
        remove_retired_entities(hass, entry, lamp.coordinator.address)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(reload_entry))
    sync_configuration_issues(hass, entry)
    if runtime.session:
        await log_interrupted_pairings(hass, runtime.session)
        for lamp in lamps.values():
            runtime.session.schedule_connect(lamp.coordinator)
    if not hass.services.has_service(DOMAIN, "capture"):
        register_services(hass)
    return True


def register_services(hass: HomeAssistant) -> None:
    def selected(call) -> tuple[RuntimeData, Lamp]:
        device = dr.async_get(hass).async_get(call.data["device_id"])
        if device is not None:
            for config in hass.config_entries.async_loaded_entries(DOMAIN):
                runtime = getattr(config, "runtime_data", None)
                lamp = runtime.lamp_for_device(device) if runtime else None
                if runtime and lamp:
                    return runtime, lamp
        raise HomeAssistantError("Select a FlinQ Orion lamp of a loaded network")

    async def action(call: ServiceCall):
        runtime, lamp = selected(call)
        coordinator = lamp.coordinator
        device = coordinator.device
        try:
            if call.service == "capture":
                if runtime.session:
                    await runtime.session.capture(coordinator, call.data["seconds"])
                else:
                    await lamp.inspector.capture(call.data["seconds"])
            elif call.service == "query_status":
                return await device.discover(include_optional=call.data["include_optional"])
            elif call.service == "query_dp":
                await device.query((call.data["dp_id"],))
            elif call.service == "dump_capabilities":
                return {
                    "capabilities": asdict(coordinator.capabilities),
                    "device": device.persist(),
                }
            elif call.service == "bind_vendor_model":
                if call.data["confirm"] is not True:
                    raise HomeAssistantError("Explicit confirmation is required for model binding")
                async with device.operation_lock:
                    await device.bind_vendor(call.data["app_key_index"])
            elif call.service == "probe_link":
                if runtime.session is None:
                    raise HomeAssistantError("Link probe requires configured mesh keys")
                return await runtime.session.probe_link(
                    coordinator, call.data["seconds"], call.data["interval"]
                )
        except HomeAssistantError:
            raise
        except Exception as err:
            coordinator.record(
                {"kind": "action_error", "action": call.service, "error": type(err).__name__}
            )
            raise HomeAssistantError(
                f"{call.service} failed ({type(err).__name__}); download diagnostics"
            ) from err
        return None

    for service in SERVICES:
        fields: dict[Any, Any] = {vol.Required("device_id"): str}
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
        elif service == "probe_link":
            fields[vol.Optional("seconds", default=120)] = vol.All(
                vol.Coerce(int), vol.Range(min=30, max=600)
            )
            fields[vol.Optional("interval", default=10)] = vol.All(
                vol.Coerce(int), vol.Range(min=5, max=60)
            )
            response = SupportsResponse.OPTIONAL
        hass.services.async_register(
            DOMAIN,
            service,
            action,
            schema=cast(Any, vol.Schema(fields)),
            supports_response=response,
        )


async def reload_entry(hass, entry):
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and runtime.config == config_snapshot(entry):
        return  # Only a detected mesh address changed; the session already uses it.
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        runtime = entry.runtime_data
        if runtime.session:
            await runtime.session.stop()
            claims = hass.data.get(f"{DOMAIN}_source_claims", {})
            if claims.get(runtime.session.identity) == entry.entry_id:
                claims.pop(runtime.session.identity, None)
        for lamp in runtime.lamps.values():
            await lamp.inspector.stop()
            await lamp.coordinator.stop()
        if not any(
            e.entry_id != entry.entry_id and getattr(e, "runtime_data", None)
            for e in hass.config_entries.async_entries(DOMAIN)
        ):
            for service in SERVICES:
                hass.services.async_remove(DOMAIN, service)
        return True
    return False
