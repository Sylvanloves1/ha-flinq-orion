"""Brand images that Home Assistant serves from the integration folder (D-020)."""

from pathlib import Path
import struct
import pytest

BRAND = Path(__file__).parents[1] / "custom_components/flinq_orion/brand"


@pytest.mark.parametrize(
    ("name", "size"),
    [("icon.png", 256), ("icon@2x.png", 512), ("dark_icon.png", 256), ("dark_icon@2x.png", 512)],
)
def test_brand_icons_are_square_png_with_transparency(name, size):
    data = (BRAND / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    width, height, depth, colour_type = struct.unpack(">IIBB", data[16:26])
    assert (width, height) == (size, size)
    assert colour_type == 6  # RGBA: transparent background
