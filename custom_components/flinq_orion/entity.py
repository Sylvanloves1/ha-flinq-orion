"""Shared identity and event-driven addition of observed capabilities."""

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .device_profiles import FLINQ_ORION_FQC8274_PROFILE as PROFILE


class OrionEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.device = coordinator.device
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=PROFILE.name,
            manufacturer=PROFILE.manufacturer,
            model=PROFILE.model,
            sw_version=PROFILE.reported_firmware,
        )

    @property
    def available(self):
        data = self.coordinator.data
        if self.device.options.get("connection_mode", "on_demand") == "on_demand":
            # Usually no link is open; a command connects. Recent advertising is the signal.
            return bool(data["available"])
        return bool(data["available"] and data.get("connected") and data.get("responsive"))


def add_when_supported(entry, async_add_entities, factory):
    coordinator = entry.runtime_data.coordinator
    added = set()

    @callback
    def discover():
        entities = []
        for key, supported, create in factory():
            if supported and key not in added:
                added.add(key)
                entities.append(create())
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()
