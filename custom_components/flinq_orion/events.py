"""Bounded diagnostics logs for a mesh network and for each lamp."""

from collections import deque
import logging
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__package__)

# Kept apart from packet records, which push connection events out of a 100-entry buffer.
LIFECYCLE_KINDS = frozenset(
    {
        "connect_attempt",
        "link_open",
        "connection_setup",
        "connection_ready",
        "connection_error",
        "disconnected",
        "gatt_cache_clear",
        "lamp_state_warning",
        "link_probe",
        "action_error",
        "refresh",
        "idle_disconnect",
        "battery_state",
        "node_address",
        "relay_fallback",
    }
)


class EventLog:
    def __init__(self):
        self.records: deque[dict] = deque(maxlen=100)
        self.connection_history: deque[dict] = deque(maxlen=60)

    def record(self, record):
        record["time"] = dt_util.utcnow().isoformat()
        self.records.append(record)
        if record.get("kind") in LIFECYCLE_KINDS:
            self.connection_history.append(record)
        _LOGGER.debug("ORION RX %s", record)
