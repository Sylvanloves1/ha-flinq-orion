"""Bounded Proxy SAR reassembly with timeout and strict type consistency."""

import time
from .errors import MeshError


class ProxySAR:
    def __init__(self):
        self.buffer = bytearray()
        self.kind = None
        self.started = 0.0

    def feed(self, data: bytes) -> tuple[int, bytes] | None:
        if not data:
            raise MeshError("Empty proxy PDU")
        sar, kind = data[0] >> 6, data[0] & 63
        if kind > 3:
            raise MeshError("Invalid proxy type")
        if sar == 0:
            self.buffer.clear()
            self.kind = None
            return kind, data[1:]
        if sar == 1:
            self.buffer = bytearray(data[1:])
            self.kind, self.started = kind, time.monotonic()
            return None
        if self.kind != kind or time.monotonic() - self.started > 20:
            self.buffer.clear()
            self.kind = None
            raise MeshError("Invalid/expired proxy SAR")
        self.buffer.extend(data[1:])
        if len(self.buffer) > 384:
            self.buffer.clear()
            self.kind = None
            raise MeshError("Proxy SAR overflow")
        if sar == 3:
            result = bytes(self.buffer)
            self.buffer.clear()
            self.kind = None
            return kind, result
        return None
