"""Bounded receive-only access segmentation; no fabricated segment ACKs."""

import time
from collections import OrderedDict
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from .crypto import k4
from .errors import MeshError, MeshDecryptionError


class ReplayWindow:
    def __init__(self, limit=256):
        self.seen = OrderedDict()
        self.limit = limit

    def accept(self, iv, src, seq):
        key = iv, src, seq
        if key in self.seen:
            return False
        self.seen[key] = None
        if len(self.seen) > self.limit:
            self.seen.popitem(last=False)
        return True


class AccessReceiver:
    def __init__(self, app_key: bytes, device_key: bytes | dict[int, bytes] | None = None):
        """device_key: one key, or {source unicast address: key} for several lamps."""
        if len(app_key) != 16:
            raise ValueError("AppKey must be 128 bits")
        keys = device_key.values() if isinstance(device_key, dict) else [device_key]
        if any(key is not None and len(key) != 16 for key in keys):
            raise ValueError("Device key must be 128 bits")
        self.app_key, self.aid = app_key, k4(app_key)
        self.device_key = device_key
        self.last_completed: tuple[int, bool] | None = None
        self.ack_info: tuple[int, int, int] | None = None
        self.pending: dict[
            tuple[int, int, int, int, int], tuple[float, int, int, dict[int, bytes]]
        ] = {}

    def receive(self, msg):
        self.last_completed = None
        self.ack_info = None
        if msg.ctl:
            return None
        lower = msg.lower
        if not lower:
            raise MeshError("Empty lower transport")
        device = not bool(lower[0] & 64)
        if device:
            source_key = (
                self.device_key.get(msg.src)
                if isinstance(self.device_key, dict)
                else self.device_key
            )
            if lower[0] & 63 or source_key is None:
                raise MeshError("Device key unavailable or invalid AKF/AID")
            key_material = source_key
        else:
            if lower[0] & 63 != self.aid:
                raise MeshError("AID mismatch")
            key_material = self.app_key
        seq, szmic = msg.seq, 0
        if lower[0] & 128:
            if len(lower) < 5:
                raise MeshError("Truncated segment")
            szmic = lower[1] >> 7
            seqzero = ((lower[1] & 127) << 6) | (lower[2] >> 2)
            seg_o = ((lower[2] & 3) << 3) | (lower[3] >> 5)
            seg_n = lower[3] & 31
            if (
                seg_o > seg_n
                or not 1 <= len(lower[4:]) <= 12
                or (seg_o < seg_n and len(lower[4:]) != 12)
            ):
                raise MeshError("Invalid segment")
            seq = (msg.seq & ~8191) | seqzero
            if seq > msg.seq:
                seq -= 8192
            if seq < 0:
                raise MeshError("Invalid SeqAuth")
            key = msg.iv_index, msg.src, msg.dst, seq, lower[0] & 127
            now = time.monotonic()
            self.pending = {k: v for k, v in self.pending.items() if now - v[0] < 20}
            if key not in self.pending:
                if len(self.pending) >= 16:
                    raise MeshError("Reassembly capacity exceeded")
                self.pending[key] = (now, szmic, seg_n, {})
            _, oldmic, oldn, segments = self.pending[key]
            if (oldmic, oldn) != (szmic, seg_n):
                del self.pending[key]
                raise MeshError("Inconsistent segments")
            if seg_o in segments and segments[seg_o] != lower[4:]:
                del self.pending[key]
                raise MeshError("Conflicting segment")
            segments[seg_o] = lower[4:]
            self.ack_info = (seqzero, sum(1 << n for n in segments), seg_n)
            if len(segments) != seg_n + 1:
                return None
            encrypted = b"".join(segments[n] for n in range(seg_n + 1))
            del self.pending[key]
        else:
            encrypted = lower[1:]
        nonce = (
            bytes((2 if device else 1, szmic << 7))
            + seq.to_bytes(3, "big")
            + msg.src.to_bytes(2, "big")
            + msg.dst.to_bytes(2, "big")
            + msg.iv_index.to_bytes(4, "big")
        )
        try:
            plain = AESCCM(key_material, tag_length=8 if szmic else 4).decrypt(
                nonce, encrypted, None
            )
            self.last_completed = (seq, device)
            return plain
        except InvalidTag as err:
            raise MeshDecryptionError("Upper transport MIC invalid") from err
