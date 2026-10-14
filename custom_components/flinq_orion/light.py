"""Light capability/state comes from reports; no optimistic state updates."""

import colorsys
from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    LightEntityFeature,
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
)
from homeassistant.exceptions import HomeAssistantError
from .entity import OrionEntity, add_when_supported
from .protocol.conversions import brightness_to_ha, decode_hsv_string
from .protocol.errors import FlinqOrionError
from .protocol.vendor_tuya import DP


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data.coordinator
    add_when_supported(
        entry,
        async_add_entities,
        lambda: [("light", c.device.supports_power, lambda: OrionLight(c))],
    )


class OrionLight(OrionEntity, LightEntity):
    _attr_name = None

    def __init__(self, coordinator):
        super().__init__(coordinator, "light")

    @property
    def supported_color_modes(self):
        modes = set()
        if self.device.supports_color:
            modes.add(ColorMode.HS)
        if self.device.supports_temperature:
            modes.add(ColorMode.COLOR_TEMP)
        if modes:
            return modes
        return {ColorMode.BRIGHTNESS} if self.device.supports_brightness else {ColorMode.ONOFF}

    @property
    def color_mode(self):
        if (
            self.device.state.get("last_color_mode") == "hs"
            and ColorMode.HS in self.supported_color_modes
        ):
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in self.supported_color_modes:
            return ColorMode.COLOR_TEMP
        return next(iter(self.supported_color_modes))

    @property
    def is_on(self):
        if self.device.mode == "vendor_dp":
            return self.device.dp(1)["value"] if "1" in self.device.report_keys else None
        return self.device.state.get("power") if "sig_8204" in self.device.report_keys else None

    @property
    def brightness(self):
        if self.device.mode == "vendor_dp":
            if self.color_mode == ColorMode.HS and self.device.supports_color:
                return round(decode_hsv_string(self.device.dp(5)["value"])[2] * 255 / 100)
            value = self.device.dp(3)
            return brightness_to_ha(value["value"]) if value and value["type"] == 2 else None
        if self.color_mode == ColorMode.HS and self.device.supports_color:
            lightness, hue, saturation = self.device.state["hsl"]
            return round(
                max(colorsys.hls_to_rgb(hue / 65535, lightness / 65535, saturation / 65535)) * 255
            )
        value = self.device.state.get("brightness")
        return (value * 255 + 32767) // 65535 if value is not None else None

    @property
    def hs_color(self):
        if not self.device.supports_color:
            return None
        if self.device.mode == "vendor_dp":
            return decode_hsv_string(self.device.dp(5)["value"])[:2]
        lightness, hue, saturation = self.device.state["hsl"]
        h, s, _ = colorsys.rgb_to_hsv(
            *colorsys.hls_to_rgb(hue / 65535, lightness / 65535, saturation / 65535)
        )
        return h * 360, s * 100

    @property
    def color_temp_kelvin(self):
        return self.device.kelvin if self.device.supports_temperature else None

    @property
    def min_color_temp_kelvin(self):
        return self.device.temperature_range[0] if self.device.temperature_range else None

    @property
    def max_color_temp_kelvin(self):
        return self.device.temperature_range[1] if self.device.temperature_range else None

    @property
    def supported_features(self):
        return LightEntityFeature.EFFECT if self.device.scenes else LightEntityFeature(0)

    @property
    def effect_list(self):
        return list(self.device.scenes) or None

    @property
    def effect(self):
        return next(
            (name for name, value in self.device.scenes.items() if value == self.device.dp(6)), None
        )

    async def async_turn_off(self, **kwargs):
        async with self.device.operation_lock:
            return await self._turn_off(**kwargs)

    async def _turn_off(self, **kwargs):
        try:
            await self.device.set_power(False)
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_on(self, **kwargs):
        async with self.device.operation_lock:
            return await self._turn_on(**kwargs)

    async def _turn_on(self, **kwargs):
        try:
            if kwargs.get(ATTR_BRIGHTNESS) == 0:
                return await self.device.set_power(False)
            # Invalid settings fail before any device change.
            if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] not in self.device.scenes:
                raise HomeAssistantError("Select a captured scene")
            if ATTR_COLOR_TEMP_KELVIN in kwargs and not self.device.supports_temperature:
                raise HomeAssistantError("Temperature mapping unavailable")
            if ATTR_HS_COLOR in kwargs and not self.device.supports_color:
                raise HomeAssistantError("Color mapping unavailable")
            if (
                ATTR_BRIGHTNESS in kwargs
                and not self.device.supports_brightness
                and ATTR_HS_COLOR not in kwargs
                and not (self.color_mode == ColorMode.HS and self.device.supports_color)
            ):
                raise HomeAssistantError("Brightness capability unavailable")
            if not kwargs or self.is_on is not True or not self.coordinator.data.get("connected"):
                await self.device.set_power(True)
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                await self.device.select_light_mode("white")
                await self.device.set_temperature(kwargs[ATTR_COLOR_TEMP_KELVIN])
            if ATTR_HS_COLOR in kwargs:
                await self.device.select_light_mode("color")
                await self.device.set_color(
                    kwargs[ATTR_HS_COLOR], kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)
                )
            elif ATTR_BRIGHTNESS in kwargs:
                if (
                    self.color_mode == ColorMode.HS
                    and self.device.supports_color
                    and ATTR_COLOR_TEMP_KELVIN not in kwargs
                ):
                    await self.device.select_light_mode("color")
                    await self.device.set_color(self.hs_color, kwargs[ATTR_BRIGHTNESS])
                else:
                    await self.device.select_light_mode("white")
                    await self.device.set_brightness(kwargs[ATTR_BRIGHTNESS])
            if ATTR_EFFECT in kwargs:
                await self.device.select_light_mode("scene")
                scene = self.device.scenes[kwargs[ATTR_EFFECT]]
                await self.device.write_dp(DP(6, scene["type"], bytes.fromhex(scene["raw"])))
        except FlinqOrionError as err:
            raise HomeAssistantError(str(err)) from err
