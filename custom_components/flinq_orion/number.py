from homeassistant.components.number import NumberEntity, NumberMode
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
                "countdown",
                c.device.mode == "vendor_dp" and bool(c.device.countdown_range),
                lambda: OrionCountdown(c),
            )
        ],
    )


class OrionCountdown(OrionEntity, NumberEntity):
    _attr_translation_key = "countdown"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, c):
        super().__init__(c, "countdown")

    @property
    def native_max_value(self):
        return self.device.countdown_range

    @property
    def native_value(self):
        return self.device.dp(7)["value"]

    async def async_set_native_value(self, value):
        try:
            await self.device.write_dp(DP(7, 2, int(value).to_bytes(4, "big")))
        except (FlinqOrionError, ValueError, OverflowError) as err:
            raise HomeAssistantError(str(err)) from err
