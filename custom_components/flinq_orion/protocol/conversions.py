"""Conversions only where evidence establishes the range/format."""


def brightness_to_ha(value: int) -> int:
    return max(1, min(255, (max(10, min(1000, value)) * 255 + 500) // 1000))


def brightness_to_tuya(value: int) -> int:
    return max(10, min(1000, (max(0, min(255, value)) * 1000 + 127) // 255))


def decode_hsv_string(value: str) -> tuple[float, float, float]:
    """Opt-in Tuya 12-hex HSV candidate; not a confirmed Orion mapping."""
    if len(value) != 12:
        raise ValueError("Expected 12 hex characters")
    h, s, v = (int(value[i : i + 4], 16) for i in (0, 4, 8))
    if h > 360 or s > 1000 or v > 1000:
        raise ValueError("HSV out of range")
    return float(h), s / 10, v / 10


def encode_hsv_string(h: float, s: float, v: float) -> str:
    if not 0 <= h <= 360 or not 0 <= s <= 100 or not 0 <= v <= 100:
        raise ValueError("HSV out of range")
    return f"{round(h):04x}{round(s * 10):04x}{round(v * 10):04x}"
