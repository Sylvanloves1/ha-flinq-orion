"""Secure Network Beacon authentication; IV transitions deliberately fail closed."""

from dataclasses import dataclass
from hmac import compare_digest
from .crypto import k1, k3, s1, cmac
from .errors import MeshDecryptionError, MeshError


@dataclass(frozen=True)
class SecureBeacon:
    iv_index: int
    iv_update: bool
    key_refresh: bool


def decode_secure_beacon(data: bytes, net_key: bytes) -> SecureBeacon:
    if len(data) != 22 or data[0] != 1 or data[1] & 0xFC:
        raise MeshError("Unsupported or malformed secure beacon")
    if not compare_digest(data[2:10], k3(net_key)):
        raise MeshDecryptionError("Beacon Network ID mismatch")
    beacon_key = k1(net_key, s1(b"nkbk"), b"id128\x01")
    if not compare_digest(data[14:], cmac(beacon_key, data[1:14])[:8]):
        raise MeshDecryptionError("Beacon authentication invalid")
    return SecureBeacon(int.from_bytes(data[10:14], "big"), bool(data[1] & 2), bool(data[1] & 1))
