from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from .entity import OrionEntity, add_when_supported
from .protocol.vendor_tuya import DP
from .protocol.errors import FlinqOrionError


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data.coordinator
    add_when_supported(
        entry,
        async_add_entities,
        lambda: [
            (
                "operating_mode",
                c.device.mode == "vendor_dp" and c.device.has_dp(2, 4),
                lambda: OrionMode(c),
            )
        ],
    )


class OrionMode(OrionEntity, SelectEntity):
    _attr_translation_key = "operating_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c):
        super().__init__(c, "operating_mode")

    @property
    def options(self):
        return [
            str(value["value"]) for value in self.device.history.get("2", []) if value["type"] == 4
        ]

    @property
    def current_option(self):
        return str(self.device.dp(2)["value"])

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError("Select an observed mode")
        try:
            await self.device.write_dp(DP(2, 4, bytes((int(option),))))
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err
