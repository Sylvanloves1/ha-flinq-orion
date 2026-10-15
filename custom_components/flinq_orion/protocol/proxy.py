"""Bounded Proxy SAR reassembly with timeout and strict type consistency."""

import time
from .errors import MeshError

NETWORK_PDU, MESH_BEACON, PROXY_CONFIGURATION, PROVISIONING_PDU = range(4)


def segments(kind: int, payload: bytes, size: int = 19) -> list[bytes]:
    """Proxy PDUs for one message: SAR in bits 7–6, type in bits 5–0 (Mesh 6.3.1).

    The default size fits the standard ATT MTU of 23 on local and remote controllers.
    """
    chunks = [payload[i : i + size] for i in range(0, len(payload), size)] or [b""]
    result = []
    for n, chunk in enumerate(chunks):
        sar = 0 if len(chunks) == 1 else 1 if n == 0 else 3 if n == len(chunks) - 1 else 2
        result.append(bytes(((sar << 6) | kind,)) + chunk)
    return result


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
