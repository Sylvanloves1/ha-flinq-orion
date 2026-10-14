"""Boolean settings from the Tuya schema (D-011). State comes from DP reports only."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from .device_profiles import ORION_DPS
from .entity import OrionEntity, add_when_supported, lamps
from .protocol.errors import FlinqOrionError

SWITCHES = (56,)


async def async_setup_entry(hass, entry, async_add_entities):
    for subentry_id, c in lamps(entry):
        add_when_supported(
            entry,
            subentry_id,
            c,
            async_add_entities,
            lambda c=c: [
                (
                    ORION_DPS[identifier].code,
                    c.device.supports_settings,
                    lambda identifier=identifier: OrionSettingSwitch(c, identifier),
                )
                for identifier in SWITCHES
            ],
        )


class OrionSettingSwitch(OrionEntity, SwitchEntity):
    def __init__(self, c, identifier):
        spec = ORION_DPS[identifier]
        super().__init__(c, spec.code)
        self.identifier = identifier
        self._attr_translation_key = spec.code

    @property
    def is_on(self):
        return self.device.setting(self.identifier)

    async def async_turn_on(self, **kwargs):
        await self._write(True)

    async def async_turn_off(self, **kwargs):
        await self._write(False)

    async def _write(self, value):
        try:
            await self.device.write_setting(self.identifier, value)
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err
