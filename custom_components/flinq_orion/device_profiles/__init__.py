"""Evidence-based Orion profile; reported DP identifiers are not capabilities."""

from dataclasses import dataclass, field

KNOWN_DPS = {
    1: "power",
    2: "mode",
    3: "brightness",
    4: "color_temperature",
    5: "color",
    6: "scene",
    7: "countdown",
}


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
    unresolved_dps: tuple[int, ...]


FLINQ_ORION_FQC8274_PROFILE = DeviceProfile(
    model="FQC8274",
    pid="1ajecbtl",
    name="FlinQ Smart Outdoor Lamp Orion",
    manufacturer="FlinQ / Tuya",
    reported_firmware="5.1",
    brightness_range=(10, 1000),
    temperature_raw_range=(0, 1000),
    unresolved_dps=(51, 52, 53, 54, 55, 56, 58, 59),
)
