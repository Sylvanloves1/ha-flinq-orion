"""Authenticated managed-flooding network PDUs (no key-refresh guessing)."""

from dataclasses import dataclass
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from .crypto import aes_block, k2
from .errors import MeshError, MeshDecryptionError


@dataclass(frozen=True)
class NetworkMessage:
    nid: int
    iv_index: int
    ctl: bool
    ttl: int
    seq: int
    src: int
    dst: int
    lower: bytes


class NetworkCodec:
    def __init__(self, net_key: bytes):
        if len(net_key) != 16:
            raise ValueError("NetKey must be 128 bits")
        self.nid, self.encryption, self.privacy = k2(net_key)

    def _privacy(self, iv: int, encrypted: bytes) -> bytes:
        return aes_block(self.privacy, bytes(5) + iv.to_bytes(4, "big") + encrypted[:7])[:6]

    def decode(self, pdu: bytes, iv_index: int, *, proxy: bool = False) -> NetworkMessage:
        if not 14 <= len(pdu) <= 29:
            raise MeshError("Invalid network PDU length")
        if pdu[0] & 0x7F != self.nid:
            raise MeshDecryptionError(
                f"NID mismatch packet=0x{pdu[0] & 0x7F:02x} expected=0x{self.nid:02x}"
            )
        iv = iv_index if (iv_index & 1) == pdu[0] >> 7 else iv_index - 1
        if iv < 0:
            raise MeshDecryptionError("Invalid IVI")
        header = bytes(a ^ b for a, b in zip(pdu[1:7], self._privacy(iv, pdu[7:]), strict=True))
        ctl = bool(header[0] & 128)
        nonce = (
            (b"\x03\x00" + header[1:] if proxy else b"\x00" + header)
            + bytes(2)
            + iv.to_bytes(4, "big")
        )
        try:
            plain = AESCCM(self.encryption, tag_length=8 if ctl else 4).decrypt(
                nonce, pdu[7:], None
            )
        except InvalidTag as err:
            raise MeshDecryptionError("Network MIC invalid") from err
        src = int.from_bytes(header[4:6], "big")
        dst = int.from_bytes(plain[:2], "big")
        if (
            not 1 <= src <= 0x7FFF
            or (dst == 0 and not proxy)
            or (proxy and (dst != 0 or not ctl or header[0] != 0x80))
        ):
            raise MeshError("Invalid mesh address")
        return NetworkMessage(
            self.nid,
            iv,
            ctl,
            header[0] & 127,
            int.from_bytes(header[1:4], "big"),
            src,
            dst,
            plain[2:],
        )

    def encode(self, msg: NetworkMessage, *, proxy: bool = False) -> bytes:
        if (
            not 0 <= msg.seq <= 0xFFFFFF
            or not 1 <= msg.src <= 0x7FFF
            or not (0 if proxy else 1) <= msg.dst <= 0xFFFF
            or (proxy and (msg.dst != 0 or not msg.ctl or msg.ttl != 0))
            or not 0 <= msg.iv_index <= 0xFFFFFFFF
            or not 0 <= msg.ttl <= 127
        ):
            raise MeshError("Invalid network fields")
        header = (
            bytes(((128 if msg.ctl else 0) | msg.ttl,))
            + msg.seq.to_bytes(3, "big")
            + msg.src.to_bytes(2, "big")
        )
        nonce = (
            (b"\x03\x00" + header[1:] if proxy else b"\x00" + header)
            + bytes(2)
            + msg.iv_index.to_bytes(4, "big")
        )
        encrypted = AESCCM(self.encryption, tag_length=8 if msg.ctl else 4).encrypt(
            nonce, msg.dst.to_bytes(2, "big") + msg.lower, None
        )
        if len(encrypted) + 7 > 29:
            raise MeshError("Network PDU exceeds bearer limit")
        obfuscated = bytes(
            a ^ b for a, b in zip(header, self._privacy(msg.iv_index, encrypted), strict=True)
        )
        return bytes((((msg.iv_index & 1) << 7) | self.nid,)) + obfuscated + encrypted
