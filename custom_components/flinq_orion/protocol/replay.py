"""Persistent authenticated message high-watermarks for a single subnet.

Upper transport reassembly happens before accepting a completed SeqAuth.
Storage errors poison reception/control rather than silently weakening replay checks.
"""

import asyncio
import time
from .errors import MeshError


class ReplayProtection:
    def __init__(self, store, limit=128):
        self.store, self.limit = store, limit
        self.entries: dict[str, tuple[int, int]] = {}
        self.lock = asyncio.Lock()
        self.failed = False
        self.started: dict[tuple[int, int, int], float] = {}

    async def load(self):
        data = await self.store.async_load()
        if data:
            entries = data.get("entries", {})
            if not isinstance(entries, dict) or len(entries) > self.limit:
                raise MeshError("Invalid replay store")
            for src, value in entries.items():
                if (
                    not isinstance(value, list)
                    or len(value) != 2
                    or not all(isinstance(n, int) for n in value)
                    or not 0 <= value[0] <= 0xFFFFFFFF
                    or not 0 <= value[1] <= 0xFFFFFF
                ):
                    raise MeshError("Invalid replay entry")
                self.entries[src] = tuple(value)

    def is_replay(self, iv: int, src: int, seq_auth: int) -> bool:
        old = self.entries.get(str(src))
        return old is not None and (iv, seq_auth) <= old

    def begin(self, iv: int, src: int, seq_auth: int) -> bool:
        now = time.monotonic()
        self.started = {key: started for key, started in self.started.items() if now - started < 20}
        key = iv, src, seq_auth
        if key in self.started:
            return True
        if self.failed or self.is_replay(iv, src, seq_auth) or len(self.started) >= 16:
            return False
        self.started[key] = now
        return True

    async def accept(self, iv: int, src: int, seq_auth: int) -> bool:
        async with self.lock:
            if self.failed:
                raise MeshError("Replay persistence failed")
            authorized = self.started.pop((iv, src, seq_auth), None)
            if self.is_replay(iv, src, seq_auth) and (
                authorized is None or time.monotonic() - authorized >= 20
            ):
                return False
            if str(src) not in self.entries and len(self.entries) >= self.limit:
                raise MeshError("Replay list full")
            entries = dict(self.entries)
            entries[str(src)] = max(entries.get(str(src), (0, -1)), (iv, seq_auth))
            try:
                await self.store.async_save({"entries": {k: list(v) for k, v in entries.items()}})
            except BaseException:
                self.failed = True
                raise
            self.entries = entries
            return True
