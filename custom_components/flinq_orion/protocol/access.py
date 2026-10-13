from dataclasses import dataclass
from .errors import MeshError


@dataclass(frozen=True)
class AccessMessage:
    opcode: int
    company: int | None
    payload: bytes


def decode_access(data: bytes) -> AccessMessage:
    if not data or data[0] == 0x7F:
        raise MeshError("Invalid opcode")
    size = 3 if data[0] >= 0xC0 else 2 if data[0] >= 0x80 else 1
    if len(data) < size:
        raise MeshError("Truncated opcode")
    return AccessMessage(
        data[0] if size != 2 else int.from_bytes(data[:2], "big"),
        int.from_bytes(data[1:3], "little") if size == 3 else None,
        data[size:],
    )
