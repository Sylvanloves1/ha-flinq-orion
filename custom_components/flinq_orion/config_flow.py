"""UI onboarding for observation or an existing, non-destructively joined mesh."""

import re
from typing import Any, cast
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol
from .const import DOMAIN, NAME, TARGET_ADDRESS


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _address = TARGET_ADDRESS

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        if discovery_info.address.upper() != TARGET_ADDRESS:
            return self.async_abort(reason="not_orion")
        self._address = discovery_info.address.upper()
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        return await self.async_step_user()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            address = user_input["address"].upper()
            if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
                errors["address"] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data={"address": address})
        return self.async_show_form(
            step_id="user",
            data_schema=cast(
                Any, vol.Schema({vol.Required("address", default=self._address): str})
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    _mesh: dict | None = None

    async def async_step_init(self, user_input=None):
        errors = {}
        old = self.config_entry.options
        if user_input is not None:
            for key in ("net_key", "app_key", "device_key"):
                value = user_input.get(key, "").strip()
                user_input[key] = value
                if value and (not re.fullmatch(r"[0-9a-fA-F]{32}", value) or value == "0" * 32):
                    errors[key] = "invalid_key"
            if bool(user_input.get("net_key")) != bool(user_input.get("app_key")):
                errors["base"] = "both_keys"
            mode = user_input.get("transport_mode", "observe")
            if mode != "observe":
                if not user_input.get("net_key") or not user_input.get("app_key"):
                    errors["base"] = "keys_required"
                source = user_input.get("source_address", 0)
                if not 1 <= source <= 0x7FFF or source == user_input.get("node_address"):
                    errors["source_address"] = "invalid_source"
                if (
                    source != old.get("source_address")
                    or user_input.get("net_key") != old.get("net_key")
                    or old.get("transport_mode", "observe") == "observe"
                ):
                    if not user_input.get("new_source_confirmed"):
                        errors["new_source_confirmed"] = "confirm_unique_source"
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if (
                        entry.entry_id != self.config_entry.entry_id
                        and entry.options.get("transport_mode", "observe") != "observe"
                        and entry.options.get("net_key", "").lower()
                        == user_input.get("net_key", "").lower()
                        and entry.options.get("source_address") == source
                    ):
                        errors["source_address"] = "source_in_use"
            if not errors:
                self._mesh = {**old, **user_input}
                return await self.async_step_profile()

        def optional(name, default):
            return vol.Optional(name, default=old.get(name, default))

        schema = vol.Schema(
            {
                vol.Required(
                    "transport_mode", default=old.get("transport_mode", "observe")
                ): vol.In(["observe", "query_only", "vendor_dp", "sig"]),
                optional("net_key", ""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                optional("app_key", ""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                optional("device_key", ""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                optional("source_address", 0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=0x7FFF)
                ),
                optional("node_address", 0x00B0): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=0x7FFF)
                ),
                optional("new_source_confirmed", False): bool,
                optional("iv_index", 0): vol.All(vol.Coerce(int), vol.Range(min=0, max=0xFFFFFFFF)),
                optional("availability_timeout", 300): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=cast(Any, schema), errors=errors)

    async def async_step_profile(self, user_input=None):
        old = self._mesh or dict(self.config_entry.options)
        errors = {}
        if user_input is not None:
            if (
                user_input.get("ct_range_confirmed")
                and not 800
                <= user_input.get("min_kelvin", 0)
                < user_input.get("max_kelvin", 0)
                <= 20000
            ):
                errors["base"] = "invalid_ct_range"
            if (
                user_input.get("countdown_seconds_confirmed")
                and user_input.get("countdown_max_seconds", 0) <= 0
            ):
                errors["base"] = "invalid_countdown_range"
            if not errors:
                return self.async_create_entry(title="", data={**old, **user_input})

        def optional(name, default):
            return vol.Optional(name, default=old.get(name, default))

        return self.async_show_form(
            step_id="profile",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        optional("hsv_encoding_confirmed", False): bool,
                        optional("white_mode_value", -1): vol.All(
                            vol.Coerce(int), vol.Range(min=-1, max=255)
                        ),
                        optional("color_mode_value", -1): vol.All(
                            vol.Coerce(int), vol.Range(min=-1, max=255)
                        ),
                        optional("scene_mode_value", -1): vol.All(
                            vol.Coerce(int), vol.Range(min=-1, max=255)
                        ),
                        optional("ct_range_confirmed", False): bool,
                        optional("min_kelvin", 0): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=20000)
                        ),
                        optional("max_kelvin", 0): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=20000)
                        ),
                        optional("scene_replay_confirmed", False): bool,
                        optional("countdown_seconds_confirmed", False): bool,
                        optional("countdown_max_seconds", 0): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=0xFFFFFFFF)
                        ),
                    }
                ),
            ),
            errors=errors,
        )
