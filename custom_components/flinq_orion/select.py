"""Enum settings from the Tuya schema (D-011). State comes from DP reports only."""

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from .device_profiles import ORION_DPS
from .entity import OrionEntity, add_when_supported
from .protocol.errors import FlinqOrionError

# Music mode needs audio data from a phone app, so it is not offered.
SELECTS = {
    2: (("white", "colour", "scene"), None),
    51: (None, EntityCategory.CONFIG),
    53: (None, EntityCategory.CONFIG),
    54: (None, EntityCategory.CONFIG),
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
                lambda identifier=identifier: OrionSettingSelect(c, identifier),
            )
            for identifier in SELECTS
        ],
    )


class OrionSettingSelect(OrionEntity, SelectEntity):
    def __init__(self, c, identifier):
        spec = ORION_DPS[identifier]
        super().__init__(c, spec.code)
        options, category = SELECTS[identifier]
        self.identifier = identifier
        self._attr_translation_key = spec.code
        self._attr_options = list(options or spec.options)
        self._attr_entity_category = category

    @property
    def current_option(self):
        value = self.device.setting(self.identifier)
        return value if value in self.options else None

    async def async_select_option(self, option):
        try:
            await self.device.write_setting(self.identifier, option)
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err
