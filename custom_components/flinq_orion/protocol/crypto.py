"""Bluetooth SIG Mesh key derivation using cryptography primitives."""

import hashlib
import hmac
from cryptography.hazmat.primitives.cmac import CMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def cmac(key: bytes, data: bytes) -> bytes:
    c = CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def s1(data: bytes) -> bytes:
    return cmac(bytes(16), data)


def k2(key: bytes, p: bytes = b"\x00") -> tuple[int, bytes, bytes]:
    t = cmac(s1(b"smk2"), key)
    t1 = cmac(t, p + b"\x01")
    t2 = cmac(t, t1 + p + b"\x02")
    t3 = cmac(t, t2 + p + b"\x03")
    return t1[-1] & 0x7F, t2, t3


def k4(key: bytes) -> int:
    return cmac(cmac(s1(b"smk4"), key), b"id6\x01")[-1] & 0x3F


def aes_block(key: bytes, data: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def k1(key: bytes, salt: bytes, info: bytes) -> bytes:
    return cmac(cmac(salt, key), info)


def k3(key: bytes) -> bytes:
    return cmac(cmac(s1(b"smk3"), key), b"id64\x01")[-8:]


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def s2(data: bytes) -> bytes:
    """Salt function of the HMAC provisioning algorithm (Mesh Protocol 1.1, 3.9.2.11)."""
    return hmac_sha256(bytes(32), data)


def k5(key: bytes, salt: bytes, info: bytes) -> bytes:
    """Key function of the HMAC provisioning algorithm (Mesh Protocol 1.1, 3.9.2.12)."""
    return hmac_sha256(hmac_sha256(salt, key), info)
