"""AES-CCM upper transport encoding and SIG lower transport control ACKs."""

from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from .crypto import k4
from .errors import MeshError


def encode_upper(
    payload: bytes, key: bytes, seq: int, src: int, dst: int, iv: int, *, device: bool = False
) -> bytes:
    if not 1 <= len(payload) <= 380 or len(key) != 16:
        raise MeshError("Invalid access length/key")
    nonce = (
        bytes((2 if device else 1, 0))
        + seq.to_bytes(3, "big")
        + src.to_bytes(2, "big")
        + dst.to_bytes(2, "big")
        + iv.to_bytes(4, "big")
    )
    return AESCCM(key, tag_length=4).encrypt(nonce, payload, None)


def lower_segments(encrypted: bytes, key: bytes, seq: int, *, device: bool = False) -> list[bytes]:
    header = 0 if device else 0x40 | k4(key)
    if len(encrypted) <= 15:
        return [bytes((header,)) + encrypted]
    parts = [encrypted[i : i + 12] for i in range(0, len(encrypted), 12)]
    if len(parts) > 32:
        raise MeshError("Too many access segments")
    seqzero = seq & 0x1FFF
    return [
        bytes(
            (
                0x80 | header,
                seqzero >> 6,
                ((seqzero & 63) << 2) | (n >> 3),
                ((n & 7) << 5) | (len(parts) - 1),
            )
        )
        + part
        for n, part in enumerate(parts)
    ]


def segment_ack(seqzero: int, block: int) -> bytes:
    if not 0 <= seqzero <= 8191 or not 0 <= block <= 0xFFFFFFFF:
        raise MeshError("Invalid segment ACK")
    return b"\x00" + (seqzero << 2).to_bytes(2, "big") + block.to_bytes(4, "big")


def decode_ack(data: bytes) -> tuple[int, int, bool]:
    if len(data) != 7 or data[0] != 0 or data[2] & 3:
        raise MeshError("Invalid segment ACK")
    field = int.from_bytes(data[1:3], "big")
    return (field >> 2) & 8191, int.from_bytes(data[3:], "big"), bool(field & 0x8000)
