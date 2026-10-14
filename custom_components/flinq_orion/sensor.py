from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import EntityCategory
from .entity import OrionEntity, lamps


async def async_setup_entry(hass, entry, async_add_entities):
    for subentry_id, coordinator in lamps(entry):
        async_add_entities([OrionRSSI(coordinator)], config_subentry_id=subentry_id)


class OrionRSSI(OrionEntity, SensorEntity):
    _attr_translation_key = "rssi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, "rssi")

    @property
    def available(self):
        return self.coordinator.data["available"]

    @property
    def native_value(self):
        return self.coordinator.data["rssi"]
