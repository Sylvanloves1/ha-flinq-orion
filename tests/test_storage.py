from unittest.mock import patch
import os
import pytest
from homeassistant.core import HomeAssistant
from custom_components.flinq_orion.storage import DurableStore
from custom_components.flinq_orion.protocol.sequence import SequenceAllocator
from custom_components.flinq_orion.protocol.errors import MeshError


@pytest.mark.asyncio
async def test_actual_disk_reservation_survives_new_instances(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    store = DurableStore(hass, "flinq_orion.sequence.test")
    first = SequenceAllocator(store, "source", 0, 16)
    await first.load()
    assert await first.next() == 0
    assert await first.next() == 1
    newstore = DurableStore(hass, "flinq_orion.sequence.test")
    restarted = SequenceAllocator(newstore, "source", 0, 16)
    await restarted.load()
    assert await restarted.next() == 16
    assert os.stat(store.path).st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_fsync_failure_is_not_swallowed(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    store = DurableStore(hass, "flinq_orion.sequence.test")
    allocator = SequenceAllocator(store, "source", 0)
    await allocator.load()
    with patch(
        "custom_components.flinq_orion.storage.os.fsync", side_effect=OSError("disk failed")
    ):
        with pytest.raises(OSError):
            await allocator.next()
    with pytest.raises(MeshError):
        await allocator.next()


@pytest.mark.asyncio
async def test_corrupt_store_fails_closed(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    store = DurableStore(hass, "flinq_orion.sequence.test")
    store.path.parent.mkdir()
    store.path.write_text("{bad json")
    with pytest.raises(ValueError):
        await store.async_load()
