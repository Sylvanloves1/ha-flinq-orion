from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from .entity import OrionEntity
from .protocol.errors import FlinqOrionError


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.coordinator.device.mode != "observe":
        async_add_entities([OrionQuery(entry.runtime_data.coordinator)])


class OrionQuery(OrionEntity, ButtonEntity):
    _attr_translation_key = "query_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c):
        super().__init__(c, "query_status")

    @property
    def available(self):
        return self.coordinator.data["available"]

    async def async_press(self):
        try:
            await self.device.discover()
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err
