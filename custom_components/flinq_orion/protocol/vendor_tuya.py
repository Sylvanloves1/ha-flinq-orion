"""Tuya documented compact SIG mesh DP framing, not serial/BLE TLV."""

from dataclasses import dataclass
from .errors import TuyaDPError

COMPANY_ID = 0x07D0
MODEL_ID = 0x0004
WRITE, WRITE_UNACK, STATUS, READ, DATA = range(0xC9, 0xCE)
FIXED_LENGTH = {1: 1, 2: 4, 4: 1}


@dataclass(frozen=True)
class DP:
    id: int
    type: int
    raw: bytes

    @property
    def value(self):
        if self.type == 1:
            return bool(self.raw[0])
        if self.type in (2, 4, 5):
            return int.from_bytes(self.raw, "big")
        if self.type == 3:
            return self.raw.decode("utf-8", errors="replace")
        return self.raw.hex()

    def diagnostics(self):
        return {"type": self.type, "raw": self.raw.hex(), "value": self.value}


def encode_dp(dp: DP) -> bytes:
    if not 1 <= dp.id <= 255 or dp.type not in range(6):
        raise TuyaDPError("Invalid DP identifier/type")
    expected = FIXED_LENGTH.get(dp.type)
    if expected is not None and len(dp.raw) != expected:
        raise TuyaDPError("Invalid fixed DP length")
    if dp.type == 1 and dp.raw not in (b"\x00", b"\x01"):
        raise TuyaDPError("Invalid boolean")
    if len(dp.raw) > 255 or (dp.type == 5 and len(dp.raw) not in (1, 2, 4)):
        raise TuyaDPError("Invalid variable DP length")
    return bytes((dp.id, dp.type)) + (bytes((len(dp.raw),)) if expected is None else b"") + dp.raw


def decode_dps(payload: bytes) -> list[DP]:
    result = []
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 2:
            raise TuyaDPError("Truncated DP header")
        identifier, kind = payload[offset : offset + 2]
        offset += 2
        if kind not in range(6):
            raise TuyaDPError("Unknown DP type; preserve entire frame")
        size = FIXED_LENGTH.get(kind)
        if size is None:
            if offset == len(payload):
                raise TuyaDPError("Missing variable length")
            size = payload[offset]
            offset += 1
        if offset + size > len(payload):
            raise TuyaDPError("Truncated DP value")
        dp = DP(identifier, kind, payload[offset : offset + size])
        encode_dp(dp)
        result.append(dp)
        offset += size
    return result


def opcode(command: int) -> bytes:
    if command not in range(WRITE, DATA + 1):
        raise TuyaDPError("Unknown vendor command")
    return bytes((command, 0xD0, 0x07))


def query(ids: tuple[int, ...] = (0,)) -> bytes:
    if not ids or len(ids) > 32 or any(not 0 <= n <= 255 for n in ids):
        raise TuyaDPError("Invalid query")
    return opcode(READ) + bytes((1, len(ids), *ids))


def write(dps: list[DP]) -> bytes:
    return opcode(WRITE) + b"\x01" + b"".join(encode_dp(dp) for dp in dps)


def decode_report(payload: bytes) -> list[DP]:
    if not payload or payload[0] != 1:
        raise TuyaDPError("Non-DP vendor message")
    return decode_dps(payload[1:])
