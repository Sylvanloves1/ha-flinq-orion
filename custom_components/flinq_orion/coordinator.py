"""One lamp of a mesh network: advertisements, state and bounded diagnostics."""

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
from .events import EventLog
from .device_profiles import DeviceCapabilities
from .protocol.network import NetworkCodec
from .protocol.transport import AccessReceiver, ReplayWindow
from .protocol.access import decode_access
from .protocol.errors import FlinqOrionError
from .protocol.controller import Response
from .protocol.advertisements import (
    PROVISIONING,
    PROXY,
    classify,
    identity_material,
    network_evidence,
    raw_service_data,
)
from .device import OrionDevice

_LOGGER = logging.getLogger(__name__)

# Low-power cycle seen on 2026-10-14: a new Node Identity random about every 180 s and no
# Network ID; after charging the lamp advertised its Network ID again.
LOW_POWER_WINDOW = 15 * 60
LOW_POWER_WAKES = 3


class LampEntry:
    """View of one lamp: the network entry options plus the lamp subentry fields.

    Lamp code reads `options` and `data` as before; only the mesh address, DeviceKey and
    MAC address differ per lamp.
    """

    def __init__(self, entry, subentry):
        self.config_entry = entry
        self.subentry_id = subentry.subentry_id
        self.title = subentry.title
        self.entry_id = f"{entry.entry_id}_{subentry.subentry_id}"
        self.data = {"address": subentry.data["address"]}
        self.options = {**entry.options, "node_address": subentry.data.get("node_address")}
        if subentry.data.get("device_key"):
            self.options["device_key"] = subentry.data["device_key"]
        else:
            self.options.pop("device_key", None)
        self.migrated = bool(subentry.data.get("migrated"))

    def async_on_unload(self, func):
        return self.config_entry.async_on_unload(func)


class OrionCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, lamp=None):
        config_entry = lamp.config_entry if lamp else entry
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=config_entry)
        # Without a lamp view the entry itself describes one lamp (tests, observe tools).
        self.entry = lamp or entry
        self.title = getattr(lamp, "title", None)
        self.address = self.entry.data["address"]
        self.capabilities = DeviceCapabilities()
        self.log = EventLog()
        self.records = self.log.records
        self.connection_history = self.log.connection_history
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
            "network_evidence": None,
            "network_evidence_counts": {},
            "battery_low_suspected": None,
        }
        self.node_identity_wakes: dict[bytes, float] = {}
        self.last_monotonic = 0.0
        self.last_response_monotonic = 0.0
        self.session = None
        self.device = OrionDevice(self)
        self.codec = self.receiver = None
        self.identity: tuple[bytes, bytes] | None = None
        options = self.entry.options
        if options.get("net_key"):
            self.identity = identity_material(bytes.fromhex(options["net_key"]))
        if options.get("net_key") and options.get("app_key"):
            self.codec = NetworkCodec(bytes.fromhex(options["net_key"]))
            self.receiver = AccessReceiver(
                bytes.fromhex(options["app_key"]),
                bytes.fromhex(options["device_key"]) if options.get("device_key") else None,
            )
        self.replay = ReplayWindow()
        if lamp:
            self.store: Store[dict] = Store(
                hass, 1, f"{DOMAIN}.{config_entry.entry_id}.{self.address}.observations"
            )
        else:
            self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.observations")
        # Before 0.4.0 one entry was one lamp with this store name.
        self.legacy_store: Store[dict] | None = (
            Store(hass, 1, f"{DOMAIN}.{config_entry.entry_id}.observations")
            if getattr(lamp, "migrated", False)
            else None
        )

    @property
    def node_address(self):
        """Mesh unicast address; None until configured or detected as proxy source."""
        return self.entry.options.get("node_address")

    async def start(self):
        saved = await self.store.async_load()
        if saved is None and self.legacy_store:
            saved = await self.legacy_store.async_load()
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
        raw = getattr(info, "raw", None)
        latest = raw_service_data(raw) if raw else None
        if latest is not None:
            record["latest_packet"] = classify(latest)
        self.record(record)
        if self.identity:
            self.check_network(info.service_data.get(PROXY), latest)
        self.track_power_state(info.service_data.get(PROXY))
        self.async_set_updated_data(dict(self.data))
        if self.session:
            self.session.schedule_connect(self)

    def track_power_state(self, proxy_data):
        """Derive "battery probably low" from the advertising pattern; no keys needed."""
        if not proxy_data:
            return
        now = time.monotonic()
        if proxy_data[0] == 0:
            self.node_identity_wakes.clear()
            suspected = False
        elif proxy_data[0] == 1 and len(proxy_data) == 17:
            self.node_identity_wakes = {
                random: seen
                for random, seen in self.node_identity_wakes.items()
                if now - seen < LOW_POWER_WINDOW
            }
            self.node_identity_wakes.setdefault(proxy_data[9:], now)
            suspected = len(self.node_identity_wakes) >= LOW_POWER_WAKES
        else:
            return
        if self.data.get("battery_low_suspected") != suspected:
            self.data["battery_low_suspected"] = suspected
            self.record({"kind": "battery_state", "battery_low_suspected": suspected})

    def check_network(self, proxy_data, latest):
        """Passive proof that the lamp still advertises this network and unicast address.

        Uses the newest proxy identity HA holds. Provisioning service data is only
        taken from the raw packet, because HA keeps merged service data from older packets.
        """
        if self.identity is None:
            return
        network_id, identity_key = self.identity
        if self.node_address is None and proxy_data and proxy_data[0] == 1:
            return  # Node Identity needs the mesh address, which is not known yet.
        result = network_evidence(proxy_data, network_id, identity_key, self.node_address or 0)
        unprovisioned = None if latest is None else PROVISIONING in latest
        counts = self.data["network_evidence_counts"]
        counts[result] = counts.get(result, 0) + 1
        if unprovisioned:
            counts["unprovisioned_packet"] = counts.get("unprovisioned_packet", 0) + 1
        previous = self.data["network_evidence"] or {}
        self.data["network_evidence"] = {
            "result": result,
            "unprovisioned_packet": unprovisioned,
            "time": dt_util.utcnow().isoformat(),
        }
        changed = (previous.get("result"), previous.get("unprovisioned_packet")) != (
            result,
            unprovisioned,
        )
        if changed and (result == "mismatch" or unprovisioned):
            self.record(
                {
                    "kind": "lamp_state_warning",
                    "network_evidence": result,
                    "unprovisioned_packet": bool(unprovisioned),
                }
            )

    @callback
    def expire(self, now):
        # The network link proves that this lamp is reachable only when it is the proxy.
        connected = bool(self.session and self.session.connected and self.session.proxy is self)
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
            self.session.schedule_connect(self)

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
        self.log.record(record)

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
            if msg.src != self.node_address:
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
