"""Orion profile from the Tuya cloud product schema (PID 1ajecbtl), see docs/protocol.md.

The schema defines data point ids, types and ranges. The SIG mesh wire encoding follows
the documented Tuya compact DP format in protocol/vendor_tuya.py (D-011).
"""

from dataclasses import dataclass, field
from ..protocol.errors import TuyaDPError
from ..protocol.vendor_tuya import DP

# Tuya compact DP types.
BOOL, VALUE, STRING, ENUM = 1, 2, 3, 4


@dataclass(frozen=True)
class DPSpec:
    id: int
    code: str
    kind: int
    minimum: int = 0
    maximum: int = 0
    # Enum values in schema order; the wire value is the index.
    options: tuple[str, ...] = ()
    writable: bool = True

    def encode(self, value) -> DP:
        """Schema-validated DP for a write; raises TuyaDPError outside the schema."""
        if self.kind == BOOL:
            return DP(self.id, BOOL, bytes((bool(value),)))
        if self.kind == ENUM:
            if value not in self.options:
                raise TuyaDPError(f"{self.code}: {value!r} is not one of {self.options}")
            return DP(self.id, ENUM, bytes((self.options.index(value),)))
        if self.kind == VALUE:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TuyaDPError(f"{self.code}: integer required")
            if not self.minimum <= value <= self.maximum:
                raise TuyaDPError(f"{self.code}: {value} outside {self.minimum}–{self.maximum}")
            return DP(self.id, VALUE, value.to_bytes(4, "big"))
        raw = str(value).encode()
        if len(raw) > 255:
            raise TuyaDPError(f"{self.code}: value too long")
        return DP(self.id, STRING, raw)

    def decode(self, reported: dict | None):
        """Value of a reported DP; None before a report or when the type differs."""
        if reported is None or reported.get("type") != self.kind:
            return None
        value = reported["value"]
        if self.kind == ENUM:
            return self.options[value] if 0 <= value < len(self.options) else None
        return value


ORION_DPS = {
    spec.id: spec
    for spec in (
        DPSpec(1, "switch_led", BOOL),
        DPSpec(2, "work_mode", ENUM, options=("white", "colour", "scene", "music")),
        DPSpec(3, "bright_value", VALUE, 10, 1000),
        DPSpec(4, "temp_value", VALUE, 0, 1000),
        DPSpec(5, "colour_data", STRING),
        DPSpec(6, "scene_data", STRING),
        DPSpec(7, "countdown", VALUE, 0, 86400),
        DPSpec(51, "device_mode", ENUM, options=("auto", "manual")),
        DPSpec(52, "pir_state", ENUM, options=("pir", "none"), writable=False),
        DPSpec(53, "cds", ENUM, options=("2000lux", "300lux", "50lux", "10lux", "5lux")),
        DPSpec(54, "pir_sensitivity", ENUM, options=("low", "middle", "high")),
        DPSpec(55, "pir_delay", VALUE, 5, 3600),
        DPSpec(56, "switch_pir", BOOL),
        DPSpec(58, "standby_time", VALUE, 1, 480),
        DPSpec(59, "standby_bright", VALUE, 0, 1000),
    )
}

KNOWN_DPS = {identifier: spec.code for identifier, spec in ORION_DPS.items()}


@dataclass
class DeviceCapabilities:
    sig_models: list[int] = field(default_factory=list)
    vendor_models: list[tuple[int, int]] = field(default_factory=list)
    known_dps: dict = field(default_factory=dict)
    unknown_dps: dict = field(default_factory=dict)

    def observe(self, dp):
        target = self.known_dps if dp.id in KNOWN_DPS else self.unknown_dps
        target[str(dp.id)] = dp.diagnostics()


@dataclass(frozen=True)
class DeviceProfile:
    model: str
    pid: str
    name: str
    manufacturer: str
    reported_firmware: str
    brightness_range: tuple[int, int]
    temperature_raw_range: tuple[int, int]
    # Kelvin range Home Assistant's Tuya integration used for this lamp; not measured.
    default_kelvin_range: tuple[int, int]
    # Bytes 8–15 of the unprovisioned device UUID. Observed once on 2026-10-14; it is not
    # the cloud PID (D-022).
    product_key: bytes


FLINQ_ORION_FQC8274_PROFILE = DeviceProfile(
    model="FQC8274",
    pid="1ajecbtl",
    name="FlinQ Smart Outdoor Lamp Orion",
    manufacturer="FlinQ / Tuya",
    reported_firmware="5.1",
    brightness_range=(10, 1000),
    temperature_raw_range=(0, 1000),
    default_kelvin_range=(2000, 6500),
    product_key=b"key5wvvr",
)
