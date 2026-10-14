"""Derived battery status; the lamp reports no battery data point (D-011, D-013)."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory
from .entity import OrionEntity, lamps


async def async_setup_entry(hass, entry, async_add_entities):
    for subentry_id, coordinator in lamps(entry):
        async_add_entities([OrionBatteryStatus(coordinator)], config_subentry_id=subentry_id)


class OrionBatteryStatus(OrionEntity, BinarySensorEntity):
    """On, shown as "Empty", when the lamp shows its low-power advertising cycle.

    Observed on 2026-10-14: with a low battery the lamp woke about every 180 s and
    advertised Node Identity with a new random each time; after charging it advertised
    its Network ID. This is an inference from advertising, not a battery measurement.
    """

    _attr_translation_key = "battery"
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c):
        super().__init__(c, "battery")

    @property
    def available(self):
        # Most useful exactly when the lamp is hard to reach.
        return True

    @property
    def is_on(self):
        return self.coordinator.data.get("battery_low_suspected")
