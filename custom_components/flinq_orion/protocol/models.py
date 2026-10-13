"""Strict Composition Data Page 0 decoder; model IDs scoped by element."""

from dataclasses import dataclass
from .errors import MeshError


@dataclass(frozen=True)
class Element:
    location: int
    sig_models: tuple[int, ...]
    vendor_models: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Composition:
    company_id: int
    product_id: int
    version_id: int
    replay_capacity: int
    features: int
    elements: tuple[Element, ...]


def decode_composition(data: bytes) -> Composition:
    if len(data) < 15 or data[0] != 0:
        raise MeshError("Invalid or unsupported composition page")
    header = [int.from_bytes(data[i : i + 2], "little") for i in range(1, 11, 2)]
    offset = 11
    elements = []
    while offset < len(data):
        if len(data) - offset < 4:
            raise MeshError("Truncated composition element")
        location = int.from_bytes(data[offset : offset + 2], "little")
        sig_count, vendor_count = data[offset + 2 : offset + 4]
        offset += 4
        if offset + 2 * sig_count + 4 * vendor_count > len(data):
            raise MeshError("Truncated composition models")
        sig = tuple(
            int.from_bytes(data[i : i + 2], "little")
            for i in range(offset, offset + 2 * sig_count, 2)
        )
        offset += 2 * sig_count
        vendor = tuple(
            (
                int.from_bytes(data[i : i + 2], "little"),
                int.from_bytes(data[i + 2 : i + 4], "little"),
            )
            for i in range(offset, offset + 4 * vendor_count, 4)
        )
        offset += 4 * vendor_count
        elements.append(Element(location, sig, vendor))
    return Composition(header[0], header[1], header[2], header[3], header[4], tuple(elements))
