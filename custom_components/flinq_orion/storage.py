"""Crash-safe small stores whose write errors propagate to cryptographic callers.

HA Store is appropriate for observations, but its async_save logs/suppresses
write errors. Sequence reservations and replay state need stricter semantics.
All filesystem I/O, fsync and directory sync run in HA's executor.
"""

import json
import os
import tempfile
from pathlib import Path


class DurableStore:
    def __init__(self, hass, key):
        if not key.startswith("flinq_orion.") or "/" in key or "\\" in key:
            raise ValueError("Invalid private storage key")
        self.hass = hass
        self.path = Path(hass.config.path(".storage", key))

    async def async_load(self):
        return await self.hass.async_add_executor_job(self._load)

    def _load(self):
        try:
            with self.path.open("rb") as stream:
                content = stream.read(65537)
        except FileNotFoundError:
            return None
        if len(content) > 65536:
            raise ValueError("Cryptographic state store too large")
        data = json.loads(content)
        if data.get("version") != 1 or not isinstance(data.get("data"), dict):
            raise ValueError("Invalid cryptographic state store")
        return data["data"]

    async def async_save(self, data):
        encoded = json.dumps({"version": 1, "data": data}, separators=(",", ":")).encode()
        if len(encoded) > 65536:
            raise ValueError("Cryptographic state store too large")
        await self.hass.async_add_executor_job(self._save, encoded)

    @staticmethod
    def _sync_dir(directory):
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _save(self, encoded):
        directory = self.path.parent
        created = not directory.exists()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if created:
            self._sync_dir(directory.parent)
        fd, temp_path = tempfile.mkstemp(prefix=self.path.name + ".", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            self._sync_dir(directory)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
