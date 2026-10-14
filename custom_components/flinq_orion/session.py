"""HA proxy-aware persistent GATT session with bounded reconnect scheduling."""

import asyncio
import hashlib
import time
import re
from homeassistant.components import bluetooth
from .storage import DurableStore
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from .const import DOMAIN, PROXY_SERVICE, PROXY_IN, PROXY_OUT
from .protocol.controller import MeshController
from .protocol.sequence import SequenceAllocator
from .protocol.replay import ReplayProtection
from .protocol.proxy import ProxySAR
from .protocol.errors import MeshError, DeviceUnavailableError, FlinqOrionError
from .protocol.beacon import decode_secure_beacon


class MeshSession:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entry = coordinator.entry
        self.client = None
        self.controller = None
        self.allocator = None
        self.lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.closed = False
        self.task = None
        self.crypto_blocked = False
        self.rx_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.rx_worker = None
        self.connection_generation = 0
        self.next_attempt = 0.0
        self.failures = 0
        self.setup_stage = "idle"
        self.link_test_pending = False
        self.link_test_lock = asyncio.Lock()
        self.key_scope = hashlib.sha256(bytes.fromhex(self.entry.options["net_key"])).hexdigest()[
            :24
        ]
        self.source = self.entry.options["source_address"]
        self.node = self.entry.options.get("node_address", 0x00B0)
        self.iv_index = self.entry.options["iv_index"]
        self.identity = f"{self.key_scope}:{self.source:04x}"
        self.store = DurableStore(self.hass, f"{DOMAIN}.sequence.{self.identity}")
        self.replay = ReplayProtection(DurableStore(self.hass, f"{DOMAIN}.replay.{self.identity}"))
        coordinator.device.ensure_connection = self.ensure_connection

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

    def schedule_connect(self):
        if (
            self.closed
            or self.crypto_blocked
            or self.coordinator.device.mode == "observe"
            or self.connected
            or (self.task and not self.task.done())
            or time.monotonic() < self.next_attempt
        ):
            return
        self.task = self.hass.async_create_background_task(
            self._background_connect(), "FlinQ Orion reconnect"
        )

    @property
    def connected(self):
        return (
            self.client is not None
            and self.client.is_connected
            and self.controller is not None
            and self.coordinator.data.get("connected", False)
        )

    def connection_error(self, err):
        """Keep bounded failure causes, stripping credentials and Bluetooth addresses."""
        causes: list[dict[str, str]] = []
        seen = set()
        while err is not None and id(err) not in seen and len(causes) < 4:
            seen.add(id(err))
            message = str(err)
            for key in ("net_key", "app_key", "device_key"):
                secret = self.entry.options.get(key)
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

    async def request_test_parameters(self, client):
        """One opt-in request; proxy GAP logs establish actual accepted values."""
        setter = getattr(client, "set_connection_params", None)
        if setter is None:
            raise MeshError("Bluetooth backend has no connection-parameter API")
        self.coordinator.record(
            {
                "kind": "link_parameter_experiment",
                "status": "requested",
                "interval_ms": 40,
                "latency": 0,
                "timeout_ms": 8000,
            }
        )
        await setter(min_interval=32, max_interval=32, latency=0, timeout=800)
        self.link_test_pending = False
        self.coordinator.record(
            {
                "kind": "link_parameter_experiment",
                "status": "api_returned",
                "accepted": "verify_in_proxy_gap_log",
            }
        )

    async def test_connection_parameters(self):
        """Apply once to this connection; never store a preference or change mesh state."""
        async with self.link_test_lock:
            self.link_test_pending = True
            try:
                if self.connected:
                    await self.request_test_parameters(self.client)
                else:
                    await self.ensure_connection()
                    if self.link_test_pending:
                        await self.request_test_parameters(self.client)
                return await self.coordinator.device.discover()
            finally:
                self.link_test_pending = False

    async def _background_connect(self):
        try:
            await self.ensure_connection()
            # Read-only discovery. No bind or DP writes happen on startup/reconnect.
            await self.coordinator.device.discover()
        except Exception as err:
            self.coordinator.record(self.connection_error(err))
            self.failures += 1
            self.next_attempt = time.monotonic() + min(300, 15 * (2 ** min(self.failures, 5)))

    def _disconnected(self, client):
        if client is not self.client:
            return
        self.coordinator.record({"kind": "disconnected", "generation": self.connection_generation})
        self.connection_generation += 1
        if self.controller:
            self.controller.close()
        self.coordinator.data["connected"] = False
        self.coordinator.device.controller = None
        self.coordinator.async_set_updated_data(dict(self.coordinator.data))
        self.next_attempt = max(self.next_attempt, time.monotonic() + 15)

    async def ensure_connection(self):
        if self.crypto_blocked:
            raise MeshError("Authenticated IV/key-refresh transition requires recovery")
        if self.connected:
            return
        async with self.lock:
            if self.closed:
                raise DeviceUnavailableError("Integration unloaded")
            if self.connected:
                return
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.coordinator.address, connectable=True
            )
            if device is None:
                raise DeviceUnavailableError(
                    "No connectable HA Bluetooth source can reach the lamp"
                )
            if self.client:
                old = self.client
                self.client = None
                await old.disconnect()
            self.setup_stage = "connect_and_discover_services"
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                "FlinQ Orion",
                disconnected_callback=self._disconnected,
                use_services_cache=False,
            )
            if self.closed:
                await client.disconnect()
                raise DeviceUnavailableError("Integration unloaded while connecting")
            self.client = client
            self.connection_generation += 1
            generation = self.connection_generation
            sar = ProxySAR()

            def notification(handle, data):
                if generation != self.connection_generation:
                    return
                try:
                    result = sar.feed(bytes(data))
                    if result:
                        self.rx_queue.put_nowait((generation, *result))
                except (MeshError, asyncio.QueueFull) as err:
                    self.coordinator.record({"kind": "proxy_error", "error": type(err).__name__})

            try:
                self.coordinator.data["gatt"] = [
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
                self.coordinator.record({"kind": "gatt", "services": self.coordinator.data["gatt"]})
                if (
                    not client.services.get_service(PROXY_SERVICE)
                    or not client.services.get_characteristic(PROXY_IN)
                    or not client.services.get_characteristic(PROXY_OUT)
                ):
                    raise MeshError("Lamp exposes no SIG Mesh Proxy service 0x1828")
                key = bytes.fromhex(self.entry.options["app_key"])
                devkey = (
                    bytes.fromhex(self.entry.options["device_key"])
                    if self.entry.options.get("device_key")
                    else None
                )
                controller = MeshController(
                    self.coordinator.codec,
                    key,
                    devkey,
                    self.source,
                    self.node,
                    self.iv_index,
                    self.allocator,
                    self.replay,
                    self.send_proxy,
                    self.coordinator.device.response,
                    self.coordinator.record,
                )
                self.controller = controller
                if self.link_test_pending:
                    self.setup_stage = "experimental_connection_parameters"
                    # Prior capture shows a second update about 0.9 s after discovery.
                    # This bounded delay permits that update before the test request.
                    await asyncio.sleep(1.25)
                    await self.request_test_parameters(client)
                self.setup_stage = "subscribe_proxy_notifications"
                self.coordinator.record({"kind": "connection_setup", "stage": self.setup_stage})
                await client.start_notify(PROXY_OUT, notification)
                self.setup_stage = "configure_proxy_filter"
                self.coordinator.record({"kind": "connection_setup", "stage": self.setup_stage})
                await controller.configure_filter()
                self.coordinator.device.controller = controller
                self.coordinator.data.update(connected=True, error=None)
                self.coordinator.async_set_updated_data(dict(self.coordinator.data))
                self.failures = 0
                self.setup_stage = "ready"
            except BaseException as err:
                self.coordinator.record(self.connection_error(err))
                if isinstance(err, Exception) and (
                    clear_cache := getattr(client, "clear_cache", None)
                ):
                    try:
                        cleared = await clear_cache()
                        self.coordinator.record(
                            {"kind": "gatt_cache_clear", "cleared": bool(cleared)}
                        )
                    except Exception as cache_err:
                        self.coordinator.record(
                            {"kind": "gatt_cache_clear", "error": type(cache_err).__name__}
                        )
                if self.controller:
                    self.controller.close()
                self.controller = None
                self.client = None
                await client.disconnect()
                raise

    async def _receive_worker(self):
        while True:
            generation, kind, payload = await self.rx_queue.get()
            try:
                if generation != self.connection_generation or not self.controller:
                    continue
                self.coordinator.record({"kind": "proxy", "type": kind, "payload": payload.hex()})
                if kind in (0, 2):
                    await self.controller.receive(kind, payload)
                elif kind == 1 and payload and payload[0] == 1:
                    beacon = decode_secure_beacon(
                        payload, bytes.fromhex(self.entry.options["net_key"])
                    )
                    self.coordinator.record(
                        {
                            "kind": "authenticated_beacon",
                            "iv_index": beacon.iv_index,
                            "iv_update": beacon.iv_update,
                            "key_refresh": beacon.key_refresh,
                        }
                    )
                    if beacon.iv_index != self.iv_index or beacon.key_refresh:
                        self.crypto_blocked = True
                        self.controller.close()
                        self.coordinator.data.update(
                            connected=False, error="authenticated_iv_or_key_refresh_change"
                        )
                        self.coordinator.async_set_updated_data(dict(self.coordinator.data))
                elif kind == 1:
                    self.coordinator.record({"kind": "unknown_beacon", "payload": payload.hex()})
            except (FlinqOrionError, ValueError) as err:
                self.coordinator.record({"kind": "decode_error", "error": str(err)})
            except Exception as err:
                self.coordinator.record({"kind": "receive_error", "error": type(err).__name__})
                if self.replay.failed:
                    if self.controller:
                        self.controller.close()
                    self.coordinator.data["connected"] = False
                    self.coordinator.async_set_updated_data(dict(self.coordinator.data))
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

    async def capture(self, seconds=20):
        await self.ensure_connection()
        self.coordinator.record({"kind": "capture", "status": "active_session", "seconds": seconds})
        await asyncio.sleep(seconds)
        self.coordinator.record({"kind": "capture", "status": "complete"})

    async def stop(self):
        self.closed = True
        if self.controller:
            self.controller.close()
        tasks = [
            task
            for task in (self.task, self.rx_worker)
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
        self.coordinator.device.controller = None
        self.coordinator.data["connected"] = False
