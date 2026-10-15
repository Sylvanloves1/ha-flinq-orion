"""Pairing (provisioning) of FlinQ Orion lamps and their configuration (D-017, D-022).

The pairing link uses PB-GATT. The Orion uses Telink's default GATT layout: after
Provisioning Complete the proxy characteristics stay at the same handles, so the
configuration continues on the same link. Without a Proxy Filter Status within 3 s the
link is opened again with a cleared GATT cache (D-022).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import os
import time
from typing import Any
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from .const import DOMAIN, PROXY_OUT
from .device_profiles import FLINQ_ORION_FQC8274_PROFILE as PROFILE
from .protocol import provisioning as prov, sig
from .protocol.advertisements import PROXY, raw_service_data, unprovisioned_device
from .protocol.controller import MeshController
from .protocol.errors import DeviceUnavailableError, FlinqOrionError, MeshError
from .protocol.models import decode_composition
from .protocol.network import NetworkCodec
from .protocol.provisioning import ProvisioningError
from .protocol.proxy import PROVISIONING_PDU, ProxySAR, segments
from .protocol.replay import ReplayProtection
from .protocol.sequence import SequenceAllocator
from .session import controller_stores, key_scope
from .storage import DurableStore

PROVISIONING_IN = "00002adb-0000-1000-8000-00805f9b34fb"
PROVISIONING_OUT = "00002adc-0000-1000-8000-00805f9b34fb"
PROXY_IN_UUID = "00002add-0000-1000-8000-00805f9b34fb"
# A Tuya bulb ignored an Invite sent directly after connecting (tuya-ble-mesh PR #3).
SETTLE_SECONDS = 1.5
INVITE_ATTEMPTS = 3
INVITE_TIMEOUT = 5
STEP_TIMEOUT = 15
# Provisioning timer of the device is at least 60 s (Mesh 5.4.4).
PAIRING_DEADLINE = 60
CONNECT_DEADLINE = 25
FILTER_TIMEOUT = 3
CONFIG_TIMEOUT = 5
# Pairing mode lasts 6 minutes (Tuya); a lamp listed for pairing must have been seen
# in pairing mode recently, because HA keeps service data of older packets.
SEEN_SECONDS = 60
# Room for controller addresses below the first lamp address.
FIRST_LAMP_ADDRESS = 0x0010
# Element count assumed for a lamp whose composition is not known.
ELEMENT_MARGIN = 8
LIGHT_MODELS = (0x1000, 0x1300, 0x1303, 0x1306, 0x1307)
PUBLISHED_MODELS = (0x1000, 0x1300, 0x1303, 0x1307)
VENDOR_MODEL = (0x07D0, 0x0004)


def pairable(info) -> bool:
    """An Orion in pairing mode, judged from the newest advertisement HA holds."""
    raw = getattr(info, "raw", None)
    if raw:
        data = raw_service_data(raw)
    elif PROXY in info.service_data:
        return False  # merged data of a provisioned lamp that once advertised 0x1827
    else:
        data = info.service_data
    device = unprovisioned_device(data)
    return device is not None and device.product_key == PROFILE.product_key


def pairable_lamps(hass, configured: set[str]) -> dict[str, Any]:
    """Orions seen in pairing mode within the last minute, by Bluetooth address."""
    now = time.monotonic()
    result = {}
    for info in bluetooth.async_discovered_service_info(hass, connectable=False):
        address = info.address.upper()
        if address in configured or now - info.time > SEEN_SECONDS:
            continue
        if pairable(info):
            result[address] = info
    return result


def allocate_unicast(elements: int, taken: list[tuple[int, int]]) -> int:
    """Lowest free block of `elements` unicast addresses outside the taken ranges."""
    address = FIRST_LAMP_ADDRESS
    while address + elements - 1 <= 0x7FFF:
        end = address + elements - 1
        clash = next(
            ((start, count) for start, count in taken if start <= end and address < start + count),
            None,
        )
        if clash is None:
            return address
        address = clash[0] + clash[1]
    raise ProvisioningError("cannot_assign_addresses", "No free unicast address range")


class PairingStore:
    """Allocated address ranges and pairings that ended after Provisioning Complete.

    Durable (atomic write with fsync): an address is never given out twice, and the
    DeviceKey of a paired lamp survives a restart during configuration.
    """

    def __init__(self, hass, scope: str):
        self.store = DurableStore(hass, f"{DOMAIN}.pairing.{scope}")
        self.allocated: list[list[int]] = []
        self.pending: list[dict] = []

    async def load(self):
        data = await self.store.async_load() or {}
        self.allocated = [list(item) for item in data.get("allocated", [])]
        self.pending = list(data.get("pending", []))

    async def _save(self):
        await self.store.async_save({"allocated": self.allocated, "pending": self.pending})

    async def allocate(self, start: int, count: int):
        self.allocated.append([start, count])
        await self._save()

    async def add_pending(self, record: dict):
        self.pending = [item for item in self.pending if item["address"] != record["address"]]
        self.pending.append(record)
        await self._save()

    async def remove_pending(self, address: str):
        self.pending = [item for item in self.pending if item["address"] != address]
        await self._save()


@dataclass
class Network:
    """What pairing needs of a mesh network, from a running session or new keys."""

    net_key: bytes
    app_key: bytes
    iv_index: int
    source: int
    codec: NetworkCodec
    allocator: SequenceAllocator
    replay: ReplayProtection
    record: Callable[[dict], None]
    lock: asyncio.Lock
    store: PairingStore
    # (first address, element count) of addresses in use: lamps, controllers.
    taken: list[tuple[int, int]] = field(default_factory=list)
    close_link: Callable[[], Awaitable[None]] | None = None


async def network_from_session(hass, session) -> Network:
    """Pair into a running network: its allocator, replay store and link lock."""
    taken = [(session.source, 1)]
    imported = session.entry.options.get("imported_source_address")
    if imported:
        taken.append((imported, 1))
    for lamp in session.lamps:
        if lamp.node_address is not None:
            elements = len((lamp.device.composition or {}).get("elements", [])) or ELEMENT_MARGIN
            taken.append((lamp.node_address, elements))
    store = PairingStore(hass, session.key_scope)
    await store.load()
    return Network(
        bytes.fromhex(session.entry.options["net_key"]),
        bytes.fromhex(session.entry.options["app_key"]),
        session.iv_index,
        session.source,
        session.codec,
        session.allocator,
        session.replay,
        session.record,
        session.lock,
        store,
        taken,
        session.close_link,
    )


def new_keys() -> tuple[bytes, bytes]:
    """Random NetKey and AppKey for a new network; an all-zero key is never used."""
    while True:
        net_key, app_key = os.urandom(16), os.urandom(16)
        if any(net_key) and any(app_key) and net_key != app_key:
            return net_key, app_key


async def new_network(hass, net_key: bytes, app_key: bytes, source: int = 1) -> Network:
    """Pair the first lamp of a new network: stores with the names the session uses."""
    scope = key_scope(net_key)
    identity = f"{scope}:{source:04x}"
    sequence_store, replay_store = controller_stores(hass, identity)
    allocator = SequenceAllocator(sequence_store, identity, 0)
    await allocator.load()
    replay = ReplayProtection(replay_store)
    await replay.load()
    store = PairingStore(hass, scope)
    await store.load()
    return Network(
        net_key,
        app_key,
        0,
        source,
        NetworkCodec(net_key),
        allocator,
        replay,
        lambda record: None,
        asyncio.Lock(),
        store,
        [(source, 1)],
    )


@dataclass
class ConfigurationResult:
    configured: bool
    composition: dict | None = None
    results: dict[str, Any] = field(default_factory=dict)


@dataclass
class PairingOutcome:
    address: str
    unicast: int
    elements: int
    device_key: str
    configured: bool = False
    composition: dict | None = None
    results: dict[str, Any] = field(default_factory=dict)


class PairingLink:
    """One GATT link to a lamp: PB-GATT first, then the mesh proxy on the same link."""

    def __init__(self, hass, address: str, record: Callable[[dict], None]):
        self.hass, self.address, self.record = hass, address, record
        self.client: Any = None
        self.sar = ProxySAR()
        self.queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(maxsize=64)
        self.controller: MeshController | None = None
        self.worker: asyncio.Task | None = None
        # A Failed PDU can arrive while nothing is awaited (for example after Start).
        self.failed: bytes | None = None

    async def connect(self):
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise ProvisioningError("not_found", "No Bluetooth source can reach the lamp")
        self.sar = ProxySAR()
        try:
            async with asyncio.timeout(CONNECT_DEADLINE):
                self.client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    "FlinQ Orion pairing",
                    max_attempts=1,
                    use_services_cache=False,
                )
        except Exception as err:
            raise ProvisioningError("not_found", "The lamp could not be reached") from err
        self.record({"kind": "pairing", "stage": "connected"})

    def _notification(self, handle, data):
        try:
            result = self.sar.feed(bytes(data))
            if result:
                kind, payload = result
                if kind == PROVISIONING_PDU and payload[:1] == bytes((prov.FAILED,)):
                    self.failed = payload
                self.queue.put_nowait(result)
        except (MeshError, asyncio.QueueFull) as err:
            self.record({"kind": "proxy_error", "error": type(err).__name__})

    async def subscribe(self, uuid: str):
        await self.client.start_notify(uuid, self._notification)

    async def send(self, kind: int, payload: bytes, uuid: str):
        if self.failed is not None:
            prov.parse(self.failed)
        if self.client is None or not self.client.is_connected:
            raise DeviceUnavailableError("Pairing link lost")
        for segment in segments(kind, payload):
            await self.client.write_gatt_char(uuid, segment, response=False)

    async def provisioning_pdu(self, timeout: float) -> tuple[int, bytes]:
        async with asyncio.timeout(timeout):
            while True:
                kind, payload = await self.queue.get()
                if kind == PROVISIONING_PDU:
                    return prov.parse(payload)

    async def expect(self, kind: int, timeout: float = STEP_TIMEOUT) -> bytes:
        try:
            received, parameters = await self.provisioning_pdu(timeout)
        except TimeoutError as err:
            raise ProvisioningError("timeout", "The lamp stopped answering") from err
        if received != kind:
            raise ProvisioningError("unexpected_pdu", f"Expected PDU {kind}, got {received}")
        return parameters

    async def provision(self, provisioner, allocate) -> tuple[int, int]:
        """Run Invite to Complete; returns (unicast address, element count)."""
        await self.subscribe(PROVISIONING_OUT)
        await asyncio.sleep(SETTLE_SECONDS)
        capabilities = None
        for attempt in range(INVITE_ATTEMPTS):
            await self.send(PROVISIONING_PDU, provisioner.invite(), PROVISIONING_IN)
            try:
                kind, capabilities = await self.provisioning_pdu(INVITE_TIMEOUT)
            except TimeoutError:
                self.record({"kind": "pairing", "stage": "invite", "attempt": attempt + 1})
                continue
            if kind != prov.CAPABILITIES:
                raise ProvisioningError("unexpected_pdu", "Expected Capabilities")
            break
        if capabilities is None:
            raise ProvisioningError("timeout", "The lamp did not answer the invitation")
        await self.send(PROVISIONING_PDU, provisioner.start(capabilities), PROVISIONING_IN)
        assert provisioner.capabilities is not None
        elements = provisioner.capabilities.elements
        self.record(
            {
                "kind": "pairing",
                "stage": "capabilities",
                "elements": elements,
                "algorithm": provisioner.algorithm,
            }
        )
        await self.send(PROVISIONING_PDU, provisioner.public_key(), PROVISIONING_IN)
        device_public = await self.expect(prov.PUBLIC_KEY)
        await self.send(PROVISIONING_PDU, provisioner.confirmation(device_public), PROVISIONING_IN)
        device_confirmation = await self.expect(prov.CONFIRMATION)
        await self.send(
            PROVISIONING_PDU, provisioner.random_pdu(device_confirmation), PROVISIONING_IN
        )
        provisioner.verify(await self.expect(prov.RANDOM))
        unicast = await allocate(elements)
        await self.send(PROVISIONING_PDU, provisioner.data(unicast), PROVISIONING_IN)
        await self.expect(prov.COMPLETE)
        self.record({"kind": "pairing", "stage": "complete", "unicast": unicast})
        return unicast, elements

    async def open_proxy(self, network: Network, unicast: int, device_key: bytes, timeout: float):
        """Mesh proxy on this link: subscribe 0x2ADE, then set the proxy filter."""
        controller = MeshController(
            network.codec,
            network.app_key,
            {unicast: device_key},
            network.source,
            unicast,
            network.iv_index,
            network.allocator,
            network.replay,
            lambda kind, payload: self.send(kind, payload, PROXY_IN_UUID),
            self._response,
            network.record,
        )
        self.controller = controller
        await self.subscribe(PROXY_OUT)
        if self.worker is None:
            self.worker = self.hass.async_create_background_task(
                self._receive(), "FlinQ Orion pairing receive"
            )
        try:
            async with asyncio.timeout(timeout):
                await controller.configure_filter()
        except TimeoutError as err:
            raise MeshError("No Proxy Filter Status on the pairing link") from err
        return controller

    async def _response(self, response):
        """Configuration answers are matched by the request; nothing else to do."""

    async def _receive(self):
        while True:
            kind, payload = await self.queue.get()
            if kind == PROVISIONING_PDU or self.controller is None:
                continue
            try:
                await self.controller.receive(kind, payload)
            except (FlinqOrionError, ValueError) as err:
                self.record({"kind": "decode_error", "error": str(err)})

    async def reopen(self):
        """Clear the GATT cache (the proxy keeps the provisioning table) and reconnect."""
        await self.close(clear_cache=True)
        await self.connect()

    async def close(self, clear_cache: bool = False):
        if self.controller:
            self.controller.close()
            self.controller = None
        client, self.client = self.client, None
        if client is None:
            return
        if clear_cache and client.is_connected and (clear := getattr(client, "clear_cache", None)):
            try:
                await clear()
            except Exception as err:
                self.record({"kind": "gatt_cache_clear", "error": type(err).__name__})
        await client.disconnect()

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        await self.close(clear_cache=True)


async def configure(
    controller, node: int, app_key: bytes, publish_address: int, record
) -> ConfigurationResult:
    """AppKey Add first (Tuya: within 30 s), then composition, binds and publication."""

    async def request(payload: bytes, opcode: int, match=None):
        def matches(response):
            return (
                response.source == node
                and response.device_key
                and response.access.company is None
                and response.access.opcode == opcode
                and (match is None or match(response.access.payload))
            )

        return await controller.request(
            payload, matches, device=True, destination=node, retries=1, timeout=CONFIG_TIMEOUT
        )

    result = ConfigurationResult(configured=False)
    status = (await request(sig.appkey_add(app_key), sig.APPKEY_STATUS)).access.payload[0]
    result.results["appkey_add"] = status
    if status != sig.STATUS_SUCCESS:
        raise MeshError(f"AppKey Add failed with status {status}")
    response = await request(sig.composition_get(), sig.COMPOSITION_STATUS)
    composition = decode_composition(response.access.payload)
    result.composition = {
        "elements": [
            {"sig_models": list(element.sig_models), "vendor_models": element.vendor_models}
            for element in composition.elements
        ]
    }
    required_ok = True
    for index, element in enumerate(composition.elements):
        address = node + index
        models: list[tuple[int, int | None]] = [
            (model, None) for model in element.sig_models if model not in sig.FOUNDATION_MODELS
        ]
        models += [(model, company) for company, model in element.vendor_models]
        # Light and vendor models first: they matter if the lamp ends configuration early.
        models.sort(
            key=lambda item: (
                item[0] not in LIGHT_MODELS and item[1] is None,
                item[1] or 0,
                item[0],
            )
        )
        for model, company in models:
            payload = sig.model_app_bind(address, model, company)
            name = f"bind_{address:04x}_{'' if company is None else f'{company:04x}_'}{model:04x}"
            try:
                response = await request(
                    payload, sig.MODEL_APP_STATUS, lambda data, p=payload: data[1:] == p[2:]
                )
                result.results[name] = response.access.payload[0]
            except MeshError as err:
                result.results[name] = type(err).__name__
            required = model in LIGHT_MODELS or (company, model) == VENDOR_MODEL
            if required and result.results[name] != sig.STATUS_SUCCESS:
                required_ok = False
        for model in PUBLISHED_MODELS:
            if model not in element.sig_models:
                continue
            payload = sig.model_publication_set(address, publish_address, model)
            name = f"publish_{address:04x}_{model:04x}"
            try:
                response = await request(
                    payload,
                    sig.MODEL_PUBLICATION_STATUS,
                    lambda data, p=payload: data[1:3] == p[1:3] and data[-2:] == p[-2:],
                )
                result.results[name] = response.access.payload[0]
            except MeshError as err:
                result.results[name] = type(err).__name__
    result.configured = required_ok
    record({"kind": "configuration", "configured": required_ok, "results": result.results})
    return result


async def pair(hass, network: Network, address: str) -> PairingOutcome:
    """Pair one lamp and configure it. Raises ProvisioningError before Complete.

    After Complete the outcome is always returned: a configuration problem leaves
    `configured` false, and "Finish configuration" can repeat it (D-022).
    """
    async with network.lock:
        if network.close_link:
            await network.close_link()
        link = PairingLink(hass, address, network.record)
        try:
            provisioner = prov.Provisioner(network.net_key, network.iv_index)

            async def allocate(elements: int) -> int:
                taken = network.taken + [(start, count) for start, count in network.store.allocated]
                unicast = allocate_unicast(elements, taken)
                await network.store.allocate(unicast, elements)
                return unicast

            try:
                async with asyncio.timeout(PAIRING_DEADLINE):
                    await link.connect()
                    unicast, elements = await link.provision(provisioner, allocate)
            except ProvisioningError:
                raise
            except TimeoutError as err:
                raise ProvisioningError("timeout", "Pairing took too long") from err
            except Exception as err:
                raise ProvisioningError("failed", f"Pairing failed ({type(err).__name__})") from err
            assert provisioner.device_key is not None
            outcome = PairingOutcome(address, unicast, elements, provisioner.device_key.hex())
            await network.store.add_pending(
                {
                    "address": address,
                    "unicast": unicast,
                    "elements": elements,
                    "device_key": outcome.device_key,
                }
            )
            try:
                try:
                    controller = await link.open_proxy(
                        network, unicast, provisioner.device_key, FILTER_TIMEOUT
                    )
                except Exception as err:
                    network.record({"kind": "pairing", "stage": "reconnect", "error": repr(err)})
                    await link.reopen()
                    controller = await link.open_proxy(
                        network, unicast, provisioner.device_key, FILTER_TIMEOUT + 2
                    )
                result = await configure(
                    controller, unicast, network.app_key, network.source, network.record
                )
                outcome.configured = result.configured
                outcome.composition = result.composition
                outcome.results = result.results
            except Exception as err:
                network.record({"kind": "configuration", "error": type(err).__name__})
                outcome.results["error"] = type(err).__name__
            return outcome
        finally:
            await link.stop()


async def finish_configuration(session, lamp) -> ConfigurationResult:
    """Repeat the configuration of a paired lamp over the normal network link."""
    if lamp.node_address is None:
        raise MeshError("The mesh address of this lamp is not known")
    await session.ensure_connection(lamp, direct=True)
    controller = session.controller
    if controller is None:
        raise DeviceUnavailableError("No mesh connection")
    async with lamp.device.operation_lock:
        return await configure(
            controller,
            lamp.node_address,
            bytes.fromhex(session.entry.options["app_key"]),
            session.source,
            session.record,
        )
