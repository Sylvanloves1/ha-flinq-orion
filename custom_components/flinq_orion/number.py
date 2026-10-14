"""Integer settings from the Tuya schema (D-011). State comes from DP reports only."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.helpers.entity import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from .device_profiles import ORION_DPS
from .entity import OrionEntity, add_when_supported
from .protocol.errors import FlinqOrionError

# identifier: (unit, raw units per displayed unit)
NUMBERS = {
    7: (UnitOfTime.SECONDS, 1),
    55: (UnitOfTime.SECONDS, 1),
    58: (UnitOfTime.MINUTES, 1),
    59: (PERCENTAGE, 10),
}


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data.coordinator
    add_when_supported(
        entry,
        async_add_entities,
        lambda: [
            (
                ORION_DPS[identifier].code,
                c.device.supports_settings,
                lambda identifier=identifier: OrionSettingNumber(c, identifier),
            )
            for identifier in NUMBERS
        ],
    )


class OrionSettingNumber(OrionEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, c, identifier):
        spec = ORION_DPS[identifier]
        # Keep the unique id and name key of the 0.2.x countdown entity.
        key = "countdown" if identifier == 7 else spec.code
        super().__init__(c, key)
        unit, self.scale = NUMBERS[identifier]
        self.identifier = identifier
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = spec.minimum / self.scale
        self._attr_native_max_value = spec.maximum / self.scale

    @property
    def native_value(self):
        value = self.device.setting(self.identifier)
        return None if value is None else value / self.scale

    async def async_set_native_value(self, value):
        try:
            await self.device.write_setting(self.identifier, round(value * self.scale))
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err
