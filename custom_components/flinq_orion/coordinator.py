"""Event-driven bounded diagnostics and optional authenticated reception."""

from collections import deque
from datetime import timedelta
import logging
import time
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from .const import DOMAIN
from .device_profiles import DeviceCapabilities
from .protocol.network import NetworkCodec
from .protocol.transport import AccessReceiver, ReplayWindow
from .protocol.access import decode_access
from .protocol.errors import FlinqOrionError
from .protocol.controller import Response
from .protocol.advertisements import classify
from .device import OrionDevice

_LOGGER = logging.getLogger(__name__)


class OrionCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        self.address = entry.data["address"]
        self.capabilities = DeviceCapabilities()
        self.records: deque[dict] = deque(maxlen=100)
        self.data = {
            "rssi": None,
            "last_seen": None,
            "source": None,
            "available": False,
            "gatt": [],
            "error": None,
            "connected": False,
            "responsive": False,
            "command_failures": 0,
            "last_response": None,
        }
        self.last_monotonic = 0.0
        self.last_response_monotonic = 0.0
        self.session = None
        self.device = OrionDevice(self)
        self.codec = self.receiver = None
        if entry.options.get("net_key") and entry.options.get("app_key"):
            self.codec = NetworkCodec(bytes.fromhex(entry.options["net_key"]))
            self.receiver = AccessReceiver(
                bytes.fromhex(entry.options["app_key"]),
                bytes.fromhex(entry.options["device_key"])
                if entry.options.get("device_key")
                else None,
            )
        self.replay = ReplayWindow()
        self.store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.observations")

    async def start(self):
        saved = await self.store.async_load()
        if saved:
            self.capabilities.known_dps = saved.get("known_dps", {})
            self.capabilities.unknown_dps = saved.get("unknown_dps", {})
            self.capabilities.sig_models = saved.get("sig_models", [])
            self.capabilities.vendor_models = saved.get("vendor_models", [])
            self.device.restore(saved.get("device", {}))
        self.entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self.advertisement,
                {"address": self.address},
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self.expire, timedelta(seconds=30))
        )
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=False)
        if info:
            self.advertisement(info, bluetooth.BluetoothChange.ADVERTISEMENT)

    @callback
    def advertisement(self, info, change):
        self.last_monotonic = time.monotonic()
        self.data.update(
            rssi=info.rssi,
            last_seen=dt_util.utcnow().isoformat(),
            source=info.source,
            available=True,
        )
        record = {
            "kind": "advertisement",
            "service_uuids": info.service_uuids,
            "mesh_service_data": classify(info.service_data),
            "service_data": {k: v.hex() for k, v in info.service_data.items()},
            "manufacturer_data": {str(k): v.hex() for k, v in info.manufacturer_data.items()},
            "rssi": info.rssi,
            "source": info.source,
        }
        self.record(record)
        self.async_set_updated_data(dict(self.data))
        if self.session:
            self.session.schedule_connect()

    @callback
    def expire(self, now):
        connected = bool(self.session and self.session.connected)
        available = connected or time.monotonic() - self.last_monotonic < self.entry.options.get(
            "availability_timeout", 300
        )
        if available != self.data["available"]:
            self.data["available"] = available
            self.async_set_updated_data(dict(self.data))

        if (
            self.session
            and not connected
            and self.last_response_monotonic
            and time.monotonic() - self.last_response_monotonic
            >= self.entry.options.get("availability_timeout", 300)
        ):
            if self.data["responsive"]:
                self.data["responsive"] = False
                self.async_set_updated_data(dict(self.data))
        if self.session and available:
            self.session.schedule_connect()

    def report_received(self):
        self.last_monotonic = time.monotonic()
        self.last_response_monotonic = self.last_monotonic
        self.data.update(
            last_seen=dt_util.utcnow().isoformat(),
            available=True,
            responsive=True,
            command_failures=0,
            last_response=dt_util.utcnow().isoformat(),
        )
        self.store.async_delay_save(self.persisted, 5)
        self.async_set_updated_data(dict(self.data))

    def record(self, record):
        record["time"] = dt_util.utcnow().isoformat()
        self.records.append(record)
        _LOGGER.debug("ORION RX %s", record)

    def network_received(self, pdu):
        self.record(
            {"kind": "network", "raw": pdu.hex(), "packet_nid": pdu[0] & 127 if pdu else None}
        )
        if not self.codec or not self.receiver:
            return
        try:
            msg = self.codec.decode(pdu, self.entry.options.get("iv_index", 0))
            if not self.replay.accept(msg.iv_index, msg.src, msg.seq):
                return
            self.record(
                {
                    "kind": "mesh",
                    "src": msg.src,
                    "dst": msg.dst,
                    "seq": msg.seq,
                    "nid": msg.nid,
                    "iv_index": msg.iv_index,
                    "ctl": msg.ctl,
                }
            )
            access = self.receiver.receive(msg)
            if access is None:
                return
            parsed = decode_access(access)
            assert self.receiver.last_completed is not None
            seq_auth, device_key = self.receiver.last_completed
            self.record(
                {
                    "kind": "access",
                    "opcode": parsed.opcode,
                    "company": parsed.company,
                    "payload": parsed.payload.hex()
                    if not device_key
                    else "**device-key payload omitted**",
                }
            )
            if msg.src != self.entry.options.get("node_address", 0x00B0):
                return
            response = Response(msg.src, msg.dst, msg.iv_index, seq_auth, device_key, parsed)

            async def process():
                try:
                    await self.device.response(response)
                except (FlinqOrionError, ValueError) as err:
                    self.record({"kind": "decode_error", "error": str(err)})

            self.hass.async_create_task(process())
        except (FlinqOrionError, ValueError) as err:
            self.record({"kind": "decode_error", "error": str(err)})

    def persisted(self):
        return {
            "known_dps": self.capabilities.known_dps,
            "unknown_dps": self.capabilities.unknown_dps,
            "sig_models": self.capabilities.sig_models,
            "vendor_models": self.capabilities.vendor_models,
            "device": self.device.persist(),
        }

    async def stop(self):
        await self.store.async_save(self.persisted())
