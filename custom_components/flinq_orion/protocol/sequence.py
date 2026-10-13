"""Durable write-ahead reservations, scoped to a unique source and NetKey."""

import asyncio
from .errors import MeshError


class SequenceAllocator:
    def __init__(self, store, identity: str, iv_index: int, block_size: int = 256):
        if not 0 <= iv_index <= 0xFFFFFFFF or not 1 <= block_size <= 4096:
            raise ValueError("Invalid allocator parameters")
        self.store, self.identity, self.iv_index = store, identity, iv_index
        self.block_size = block_size
        self._next = self._end = 0
        self._lock = asyncio.Lock()
        self._loaded = False
        self._failed = False

    async def load(self):
        async with self._lock:
            if self._loaded:
                return
            data = await self.store.async_load()
            if data is not None:
                if data.get("identity") != self.identity or data.get("iv_index") != self.iv_index:
                    raise MeshError("Sequence identity/IV mismatch; explicit recovery required")
                end = data.get("reserved_until")
                if not isinstance(end, int) or not 0 <= end <= 0x1000000:
                    raise MeshError("Corrupt sequence store")
                self._next = self._end = end
            self._loaded = True

    async def next(self) -> int:
        if not self._loaded:
            raise MeshError("Sequence allocator not loaded")
        async with self._lock:
            if self._failed:
                raise MeshError("Persistence failed; restart and recover before transmitting")
            if self._next == self._end:
                end = min(self._next + self.block_size, 0x1000000)
                if end == self._next:
                    raise MeshError("Sequence exhausted; validated IV Update required")
                try:
                    await self.store.async_save(
                        {
                            "identity": self.identity,
                            "iv_index": self.iv_index,
                            "reserved_until": end,
                        }
                    )
                except BaseException:
                    self._failed = True
                    raise
                self._end = end
            result = self._next
            self._next += 1
            return result

    def diagnostics(self):
        return {
            "next": self._next,
            "reserved_until": self._end,
            "iv_index": self.iv_index,
            "failed": self._failed,
        }
