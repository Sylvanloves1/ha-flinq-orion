from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, NAME
from .device_profiles import FLINQ_ORION_FQC8274_PROFILE as PROFILE


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OrionRSSI(entry.runtime_data.coordinator)])


class OrionRSSI(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "rssi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_rssi"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=NAME,
            manufacturer="FlinQ / Tuya",
            model="FQC8274",
            sw_version=PROFILE.reported_firmware,
        )

    @property
    def available(self):
        return self.coordinator.data["available"]

    @property
    def native_value(self):
        return self.coordinator.data["rssi"]
