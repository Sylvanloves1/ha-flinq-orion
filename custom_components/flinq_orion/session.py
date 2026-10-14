"""Mesh session of one network: one controller identity and one GATT link (D-015).

The link goes to one lamp, which acts as proxy for the others. On demand (default) the
link opens for a command and closes after idle time; persistent mode keeps it open.
"""

import asyncio
from functools import partial
import hashlib
import time
import re
from homeassistant.components import bluetooth
from homeassistant.core import callback
from .storage import DurableStore
from bleak_retry_connector import (
    MAX_CONNECT_ATTEMPTS,
    BleakClientWithServiceCache,
    establish_connection,
)
from .const import DOMAIN, PROXY_SERVICE, PROXY_IN, PROXY_OUT
from .coordinator import LampEntry
from .events import EventLog
from .protocol.controller import MeshController
from .protocol.network import NetworkCodec
from .protocol.sequence import SequenceAllocator
from .protocol.replay import ReplayProtection
from .protocol.proxy import ProxySAR
from .protocol.errors import MeshError, DeviceUnavailableError, FlinqOrionError
from .protocol.beacon import decode_secure_beacon

# On-demand mode for the battery-powered lamp (D-009, D-010).
IDLE_DISCONNECT_SECONDS = 60
REFRESH_SECONDS = 30 * 60
# One 20 s connection attempt plus margin. Without a deadline, bleak-retry-connector
# retries "device not seen" errors up to nine times.
ON_DEMAND_CONNECT_DEADLINE = 25


class MeshSession:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.lamps: list = []
        self.log = EventLog()
        self.client = None
        self.controller = None
        self.proxy = None  # lamp coordinator that carries the current link
        self.allocator = None
        self.lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.closed = False
        self.tasks: dict[str, asyncio.Task] = {}
        self.crypto_blocked = False
        self.rx_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.rx_worker = None
        self.connection_generation = 0
        self.next_attempt: dict[str, float] = {}
        self.failures: dict[str, int] = {}
        self.last_refresh: dict[str, float] = {}
        self.setup_stage = "idle"
        self.link_opened: float | None = None
        self.on_demand = self.entry.options.get("connection_mode", "on_demand") == "on_demand"
        self.idle_handle: asyncio.TimerHandle | None = None
        net_key = bytes.fromhex(self.entry.options["net_key"])
        self.codec = NetworkCodec(net_key)
        self.key_scope = hashlib.sha256(net_key).hexdigest()[:24]
        self.source = self.entry.options["source_address"]
        self.iv_index = self.entry.options["iv_index"]
        self.identity = f"{self.key_scope}:{self.source:04x}"
        # Store names are unchanged since 0.2: the controller identity keeps its sequence.
        self.store = DurableStore(self.hass, f"{DOMAIN}.sequence.{self.identity}")
        self.replay = ReplayProtection(DurableStore(self.hass, f"{DOMAIN}.replay.{self.identity}"))

    def record(self, record):
        self.log.record(record)

    def attach(self, lamp):
        """Register a lamp coordinator; its device sends through this session."""
        self.lamps.append(lamp)
        lamp.session = self
        lamp.device.ensure_connection = partial(self.ensure_connection, lamp)
        lamp.device.reconnect_direct = partial(self.reconnect_direct, lamp)
        lamp.device.activity = self.touch

    async def initialize(self):
        existing = await self.store.async_load()
        if existing is None and (
            self.entry.data.get("sequence_scope") == self.identity
            or not self.entry.options.get("new_source_confirmed")
        ):
            raise MeshError("Sequence store missing: allocate a new unused controller address")
        self.allocator = SequenceAllocator(self.store, self.identity, self.iv_index)
        await self.allocator.load()
        # Persist a reservation before any connection can transmit; first value is burned.
        await self.allocator.next()
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, "sequence_scope": self.identity}
        )
        await self.replay.load()
        self.rx_worker = self.hass.async_create_background_task(
            self._receive_worker(), "FlinQ Orion receive"
        )

    def schedule_connect(self, lamp):
        """Called on advertisements and every 30 s while the lamp is seen.

        Persistent mode keeps a link. On-demand mode only connects for a status refresh
        of this lamp when one is due; commands connect through ensure_connection.
        """
        task = self.tasks.get(lamp.address)
        if (
            self.closed
            or self.crypto_blocked
            or lamp.device.mode == "observe"
            or (task and not task.done())
            or time.monotonic() < self.next_attempt.get(lamp.address, 0.0)
        ):
            return
        if self.on_demand:
            last = self.last_refresh.get(lamp.address)
            if last is None or time.monotonic() - last >= REFRESH_SECONDS:
                self.tasks[lamp.address] = self.hass.async_create_background_task(
                    self._refresh(lamp), "FlinQ Orion refresh"
                )
            return
        if self.connected:
            # The link may go to another lamp: read this lamp's status once through it.
            if lamp.address not in self.last_refresh:
                self.tasks[lamp.address] = self.hass.async_create_background_task(
                    self._refresh(lamp), "FlinQ Orion refresh"
                )
            return
        self.tasks[lamp.address] = self.hass.async_create_background_task(
            self._background_connect(lamp), "FlinQ Orion reconnect"
        )

    @property
    def connected(self):
        return (
            self.client is not None
            and self.client.is_connected
            and self.controller is not None
            and not self.controller.closed
        )

    def connection_error(self, err):
        """Keep bounded failure causes, stripping credentials and Bluetooth addresses."""
        secrets = [self.entry.options.get(key) for key in ("net_key", "app_key", "device_key")]
        secrets += [lamp.entry.options.get("device_key") for lamp in self.lamps]
        causes: list[dict[str, str]] = []
        seen = set()
        while err is not None and id(err) not in seen and len(causes) < 4:
            seen.add(id(err))
            message = str(err)
            for secret in secrets:
                if secret:
                    message = re.sub(re.escape(secret), "**REDACTED**", message, flags=re.I)
            message = re.sub(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", "**REDACTED**", message)
            causes.append({"error": type(err).__name__, "message": message[:1000]})
            err = err.__cause__ or err.__context__
        return {
            "kind": "connection_error",
            "error": causes[0]["error"],
            "stage": getattr(self, "setup_stage", "unknown"),
            "causes": causes,
        }

    async def _background_connect(self, lamp):
        try:
            await self.ensure_connection(lamp)
            # Read-only discovery. No bind or DP writes happen on startup/reconnect.
            await lamp.device.discover()
        except Exception as err:
            self._failed(lamp, err)
            return
        self.last_refresh[lamp.address] = time.monotonic()

    async def _refresh(self, lamp):
        """Read-only status refresh; the idle timer closes the link afterwards."""
        try:
            results = await lamp.device.discover()
        except Exception as err:
            self._failed(lamp, err)
            return
        self.failures[lamp.address] = 0
        self.last_refresh[lamp.address] = time.monotonic()
        lamp.record({"kind": "refresh", "results": results})

    def _failed(self, lamp, err):
        lamp.record(self.connection_error(err))
        failures = self.failures.get(lamp.address, 0) + 1
        self.failures[lamp.address] = failures
        self.next_attempt[lamp.address] = time.monotonic() + min(300, 15 * (2 ** min(failures, 5)))

    def _disconnected(self, client):
        if client is not self.client:
            return
        self.record(
            {
                "kind": "disconnected",
                "generation": self.connection_generation,
                "stage": self.setup_stage,
                "link_lifetime_s": self.link_lifetime(),
            }
        )
        self._mark_disconnected()
        for lamp in self.lamps:
            self.next_attempt[lamp.address] = max(
                self.next_attempt.get(lamp.address, 0.0), time.monotonic() + 15
            )

    def _mark_disconnected(self, error=None):
        self._cancel_idle()
        self.connection_generation += 1
        if self.controller:
            self.controller.close()
        self.proxy = None
        for lamp in self.lamps:
            lamp.data["connected"] = False
            if error:
                lamp.data["error"] = error
            lamp.device.controller = None
            lamp.async_set_updated_data(dict(lamp.data))

    def touch(self):
        """Restart the idle timer after activity. Persistent mode keeps the link."""
        if not self.on_demand or self.closed:
            return
        self._cancel_idle()
        self.idle_handle = self.hass.loop.call_later(IDLE_DISCONNECT_SECONDS, self._idle_expired)

    def _cancel_idle(self):
        if self.idle_handle:
            self.idle_handle.cancel()
            self.idle_handle = None

    def _requests_active(self):
        return any(lamp.device.operation_lock.locked() for lamp in self.lamps) or bool(
            self.controller and self.controller.request_lock.locked()
        )

    def _tasks_running(self):
        return any(not task.done() for task in self.tasks.values())

    @callback
    def _idle_expired(self):
        self.idle_handle = None
        if not self.connected:
            return
        if self.lock.locked() or self._requests_active() or self._tasks_running():
            self.touch()
            return
        self.hass.async_create_background_task(self._idle_disconnect(), "FlinQ Orion idle")

    async def _idle_disconnect(self):
        async with self.lock:
            if not self.connected:
                return
            if self._requests_active():
                self.touch()
                return
            self.record({"kind": "idle_disconnect", "link_lifetime_s": self.link_lifetime()})
            await self._close_link()

    async def _close_link(self):
        client = self.client
        # Detach first: the disconnect callback ignores a link this session closes.
        self.client = None
        self._mark_disconnected()
        if client is not None:
            await client.disconnect()

    def link_lifetime(self):
        if self.link_opened is None:
            return None
        return round(time.monotonic() - self.link_opened, 2)

    def _target(self, lamp, direct):
        """Lamp to connect to: this lamp, or another reachable lamp of the network.

        A lamp whose mesh address is unknown is always connected directly, because the
        address is learnt from its Proxy Filter Status.
        """
        if direct or lamp.node_address is None:
            return lamp
        candidates = [lamp] + sorted(
            (other for other in self.lamps if other is not lamp),
            key=lambda other: other.last_monotonic,
            reverse=True,
        )
        for candidate in candidates:
            if bluetooth.async_ble_device_from_address(
                self.hass, candidate.address, connectable=True
            ):
                return candidate
        return lamp

    def _can_use_link(self, lamp, direct):
        if not self.connected:
            return False
        if self.proxy is lamp:
            return True
        return not direct and lamp.node_address is not None

    async def reconnect_direct(self, lamp):
        """Connect to this lamp itself; False when it already carries the link."""
        if self.connected and self.proxy is lamp:
            return False
        lamp.record({"kind": "relay_fallback", "proxy": getattr(self.proxy, "title", None)})
        await self.ensure_connection(lamp, direct=True)
        return True

    async def ensure_connection(self, lamp, direct=False):
        if self.crypto_blocked:
            raise MeshError("Authenticated IV/key-refresh transition requires recovery")
        if self._can_use_link(lamp, direct):
            self.touch()
            return
        async with self.lock:
            if self.closed:
                raise DeviceUnavailableError("Integration unloaded")
            if self._can_use_link(lamp, direct):
                self.touch()
                return
            target = self._target(lamp, direct)
            device = bluetooth.async_ble_device_from_address(
                self.hass, target.address, connectable=True
            )
            if device is None:
                raise DeviceUnavailableError(
                    "No connectable HA Bluetooth source can reach the lamp"
                )
            if self.client:
                await self._close_link()
            await self._open_link(target, device)

    async def _open_link(self, target, device):
        self.setup_stage = "connect_and_discover_services"
        self.link_opened = None
        # 0.2.1 policy: reuse services HA already holds so the ESPHome proxy can take its
        # cached path. No logged uncached proxy connection to this lamp lasted over 21 s.
        self.record({"kind": "connect_attempt", "services_cache": "allowed", "lamp": target.title})
        try:
            async with asyncio.timeout(ON_DEMAND_CONNECT_DEADLINE if self.on_demand else None):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    "FlinQ Orion",
                    disconnected_callback=self._disconnected,
                    max_attempts=1 if self.on_demand else MAX_CONNECT_ATTEMPTS,
                )
        except FlinqOrionError:
            raise
        except Exception as err:
            raise DeviceUnavailableError(
                "Lamp not reachable over Bluetooth; it may be out of range or its battery "
                "may be low"
            ) from err
        if self.closed:
            await client.disconnect()
            raise DeviceUnavailableError("Integration unloaded while connecting")
        self.client = client
        self.link_opened = time.monotonic()
        self.connection_generation += 1
        generation = self.connection_generation
        self.record({"kind": "link_open", "generation": generation, "lamp": target.title})
        sar = ProxySAR()
        stale_table = False

        def notification(handle, data):
            if generation != self.connection_generation:
                return
            try:
                result = sar.feed(bytes(data))
                if result:
                    self.rx_queue.put_nowait((generation, *result))
            except (MeshError, asyncio.QueueFull) as err:
                self.record({"kind": "proxy_error", "error": type(err).__name__})

        try:
            target.data["gatt"] = [
                {
                    "uuid": service.uuid,
                    "characteristics": [
                        {
                            "uuid": c.uuid,
                            "handle": getattr(c, "handle", None),
                            "properties": c.properties,
                            "descriptors": [
                                {"uuid": d.uuid, "handle": d.handle}
                                for d in getattr(c, "descriptors", [])
                            ],
                        }
                        for c in service.characteristics
                    ],
                }
                for service in client.services
            ]
            self.record({"kind": "gatt", "services": target.data["gatt"]})
            if (
                not client.services.get_service(PROXY_SERVICE)
                or not client.services.get_characteristic(PROXY_IN)
                or not client.services.get_characteristic(PROXY_OUT)
            ):
                stale_table = True
                raise MeshError("Lamp exposes no SIG Mesh Proxy service 0x1828")
            controller = MeshController(
                self.codec,
                bytes.fromhex(self.entry.options["app_key"]),
                self._device_keys(),
                self.source,
                target.node_address,
                self.iv_index,
                self.allocator,
                self.replay,
                self.send_proxy,
                self.dispatch,
                self.record,
            )
            controller.allowed_sources |= self._known_nodes()
            self.controller = controller
            self.setup_stage = "subscribe_proxy_notifications"
            self.record({"kind": "connection_setup", "stage": self.setup_stage})
            await client.start_notify(PROXY_OUT, notification)
            self.setup_stage = "configure_proxy_filter"
            self.record({"kind": "connection_setup", "stage": self.setup_stage})
            proxy_source = await controller.configure_filter()
            if proxy_source is not None:
                controller.node = proxy_source
            self._learn_node(target, proxy_source)
            self.proxy = target
            for lamp in self.lamps:
                lamp.device.controller = controller
                lamp.data.update(connected=True, error=None)
                lamp.async_set_updated_data(dict(lamp.data))
            self.failures[target.address] = 0
            self.setup_stage = "ready"
            self.record(
                {
                    "kind": "connection_ready",
                    "generation": generation,
                    "setup_s": self.link_lifetime(),
                    "proxy_node": proxy_source,
                }
            )
            self.touch()
        except BaseException as err:
            self.record({**self.connection_error(err), "link_lifetime_s": self.link_lifetime()})
            # Clear only on a table without the proxy service. Clearing after a link
            # loss forces the next connection onto the uncached proxy path.
            if stale_table and (clear_cache := getattr(client, "clear_cache", None)):
                try:
                    cleared = await clear_cache()
                    self.record({"kind": "gatt_cache_clear", "cleared": bool(cleared)})
                except Exception as cache_err:
                    self.record({"kind": "gatt_cache_clear", "error": type(cache_err).__name__})
            if self.controller:
                self.controller.close()
            self.controller = None
            self.client = None
            await client.disconnect()
            raise

    def _device_keys(self):
        keys = {}
        for lamp in self.lamps:
            if lamp.node_address is not None and lamp.entry.options.get("device_key"):
                keys[lamp.node_address] = bytes.fromhex(lamp.entry.options["device_key"])
        return keys

    def _known_nodes(self):
        return {lamp.node_address for lamp in self.lamps if lamp.node_address is not None}

    def _learn_node(self, lamp, node):
        """Use the proxy source as the lamp's mesh address and store it with the lamp."""
        if node is None or lamp.node_address == node:
            return
        lamp.record({"kind": "node_address", "previous": lamp.node_address, "detected": node})
        lamp.entry.options["node_address"] = node
        view = lamp.entry
        if isinstance(view, LampEntry) and view.subentry_id in view.config_entry.subentries:
            subentry = view.config_entry.subentries[view.subentry_id]
            self.hass.config_entries.async_update_subentry(
                view.config_entry, subentry, data={**subentry.data, "node_address": node}
            )
        if self.controller:
            self.controller.allowed_sources.add(node)
            if isinstance(self.controller.device_key, dict):
                self.controller.device_key.update(self._device_keys())

    def lamp_for(self, address):
        for lamp in self.lamps:
            node = lamp.node_address
            if node is None:
                continue
            elements = len((lamp.device.composition or {}).get("elements", [])) or 1
            if node <= address < node + elements:
                return lamp
        return None

    async def dispatch(self, response):
        lamp = self.lamp_for(response.source)
        if lamp is None:
            self.record({"kind": "unknown_node", "src": response.source})
            return
        await lamp.device.response(response)

    async def _receive_worker(self):
        while True:
            generation, kind, payload = await self.rx_queue.get()
            try:
                if generation != self.connection_generation or not self.controller:
                    continue
                self.record({"kind": "proxy", "type": kind, "payload": payload.hex()})
                if kind in (0, 2):
                    await self.controller.receive(kind, payload)
                elif kind == 1 and payload and payload[0] == 1:
                    beacon = decode_secure_beacon(
                        payload, bytes.fromhex(self.entry.options["net_key"])
                    )
                    self.record(
                        {
                            "kind": "authenticated_beacon",
                            "iv_index": beacon.iv_index,
                            "iv_update": beacon.iv_update,
                            "key_refresh": beacon.key_refresh,
                        }
                    )
                    if beacon.iv_index != self.iv_index or beacon.key_refresh:
                        self.crypto_blocked = True
                        self._mark_disconnected("authenticated_iv_or_key_refresh_change")
                elif kind == 1:
                    self.record({"kind": "unknown_beacon", "payload": payload.hex()})
            except (FlinqOrionError, ValueError) as err:
                self.record({"kind": "decode_error", "error": str(err)})
            except Exception as err:
                self.record({"kind": "receive_error", "error": type(err).__name__})
                if self.replay.failed:
                    self._mark_disconnected()
            finally:
                self.rx_queue.task_done()

    async def send_proxy(self, kind, payload):
        async with self.write_lock:
            client = self.client
            if self.closed or client is None or not client.is_connected:
                raise DeviceUnavailableError("GATT connection lost")
            # Standard ATT MTU 23 works for local and remote BLE controllers.
            chunks = [payload[i : i + 19] for i in range(0, len(payload), 19)]
            for n, chunk in enumerate(chunks):
                sar = 0 if len(chunks) == 1 else 1 if n == 0 else 3 if n == len(chunks) - 1 else 2
                await client.write_gatt_char(
                    PROXY_IN, bytes(((sar << 6) | kind,)) + chunk, response=False
                )

    async def probe_link(self, lamp, seconds=120, interval=10):
        """Read-only status reads at a fixed interval to measure how long links carry traffic.

        Each read may reconnect through the normal path. Failures are measurements and do
        not stop the probe; cancellation does.
        """
        summary: dict = {"probes": 0, "responses": 0, "failures": {}, "longest_link_s": 0.0}
        generations = set()
        deadline = time.monotonic() + seconds
        while not self.closed:
            started = time.monotonic()
            try:
                await lamp.device.probe()
                result = "response"
                summary["responses"] += 1
            except Exception as err:
                result = type(err).__name__
                summary["failures"][result] = summary["failures"].get(result, 0) + 1
            summary["probes"] += 1
            age = self.link_lifetime() if self.connected else None
            if age is not None:
                generations.add(self.connection_generation)
                summary["longest_link_s"] = max(summary["longest_link_s"], age)
            lamp.record(
                {
                    "kind": "link_probe",
                    "n": summary["probes"],
                    "result": result,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "generation": self.connection_generation if age is not None else None,
                    "link_age_s": age,
                    "via": getattr(self.proxy, "title", None),
                }
            )
            if time.monotonic() + interval > deadline:
                break
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
        summary["links_used"] = len(generations)
        return summary

    async def capture(self, lamp, seconds=20):
        await self.ensure_connection(lamp, direct=True)
        lamp.record({"kind": "capture", "status": "active_session", "seconds": seconds})
        await asyncio.sleep(seconds)
        lamp.record({"kind": "capture", "status": "complete"})

    async def stop(self):
        self.closed = True
        self._cancel_idle()
        if self.controller:
            self.controller.close()
        tasks = [
            task
            for task in (*self.tasks.values(), self.rx_worker)
            if task and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.client:
            client = self.client
            self.client = None
            await client.disconnect()
        for lamp in self.lamps:
            lamp.device.controller = None
            lamp.data["connected"] = False
