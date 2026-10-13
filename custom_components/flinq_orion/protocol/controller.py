"""Local GATT mesh client. No provisioning/reset or guessed vendor opcodes."""

import asyncio
from dataclasses import dataclass
from collections import OrderedDict
from .access import AccessMessage, decode_access
from .commands import CommandChannel
from .network import NetworkMessage
from .transport import AccessReceiver, ReplayWindow
from .transmit import encode_upper, lower_segments, segment_ack, decode_ack
from .errors import MeshError, MeshTimeoutError


@dataclass(frozen=True)
class Response:
    source: int
    destination: int
    iv_index: int
    sequence: int
    device_key: bool
    access: AccessMessage


class MeshController:
    def __init__(
        self,
        codec,
        app_key,
        device_key,
        source,
        node,
        iv_index,
        allocator,
        replay,
        send_proxy,
        on_response,
        log,
    ):
        if not 1 <= source <= 0x7FFF or not 1 <= node <= 0x7FFF or source == node:
            raise MeshError("Invalid controller/node address")
        self.codec, self.app_key, self.device_key = codec, app_key, device_key
        self.source, self.node, self.iv_index = source, node, iv_index
        self.allocator, self.replay = allocator, replay
        self.send_proxy, self.on_response, self.log = send_proxy, on_response, log
        self.receiver = AccessReceiver(app_key, device_key)
        self.channel = CommandChannel(self.send_access)
        self.wire_lock = asyncio.Lock()
        self.rx_lock = asyncio.Lock()
        self.network_dedup = ReplayWindow(limit=256)
        self.tx_segments = None
        self.filter_future = None
        self.completed: OrderedDict[tuple, tuple[int, int, int]] = OrderedDict()
        self.closed = False
        self.destination = node
        self.allowed_sources = {node}
        self.device_command = False

    def _ready(self):
        if self.closed or self.replay.failed:
            raise MeshError("Controller closed or replay persistence failed")

    async def _network(self, lower, *, ctl=False, proxy=False, destination=None):
        self._ready()
        async with self.wire_lock:
            seq = await self.allocator.next()
            msg = NetworkMessage(
                self.codec.nid,
                self.iv_index,
                ctl,
                0 if proxy else 5,
                seq,
                self.source,
                0 if proxy else (destination or self.destination),
                lower,
            )
            await self.send_proxy(2 if proxy else 0, self.codec.encode(msg, proxy=proxy))
            self.log(
                {
                    "kind": "tx",
                    "seq": seq,
                    "iv_index": self.iv_index,
                    "src": self.source,
                    "dst": msg.dst,
                    "nid": msg.nid,
                    "ctl": ctl,
                    "proxy": proxy,
                }
            )
            return seq

    async def configure_filter(self):
        for lower, expected_count in (
            (b"\x00\x00", 0),
            (b"\x01" + self.source.to_bytes(2, "big"), 1),
        ):
            future = asyncio.get_running_loop().create_future()
            self.filter_future = future, expected_count
            try:
                await self._network(lower, ctl=True, proxy=True)
                async with asyncio.timeout(5):
                    await future
            except TimeoutError as err:
                raise MeshTimeoutError("No authenticated Proxy Filter Status") from err
            finally:
                self.filter_future = None
                if not future.done():
                    future.cancel()

    async def send_access(self, payload: bytes):
        self._ready()
        device = self.device_command
        key = self.device_key if device else self.app_key
        if key is None:
            raise MeshError("Device key required")
        async with self.wire_lock:
            first_seq = await self.allocator.next()
            encrypted = encode_upper(
                payload, key, first_seq, self.source, self.destination, self.iv_index, device=device
            )
            parts = lower_segments(encrypted, key, first_seq, device=device)
            state = {
                "seqzero": first_seq & 8191,
                "mask": 0,
                "event": asyncio.Event(),
                "cancelled": False,
                "full": (1 << len(parts)) - 1,
                "destination": self.destination,
            }
            if len(parts) > 1:
                self.tx_segments = state
            msg = NetworkMessage(
                self.codec.nid,
                self.iv_index,
                False,
                5,
                first_seq,
                self.source,
                self.destination,
                parts[0],
            )
            try:
                await self.send_proxy(0, self.codec.encode(msg))
            except BaseException:
                self.tx_segments = None
                raise
            self.log(
                {
                    "kind": "tx_access",
                    "seq": first_seq,
                    "src": self.source,
                    "dst": self.destination,
                    "opcode": decode_access(payload).opcode,
                    "device_key": device,
                    "segments": len(parts),
                }
            )
        if len(parts) == 1:
            return
        try:
            for part in parts[1:]:
                if self.allocator.diagnostics()["next"] - first_seq >= 8192:
                    raise MeshError("Segment transaction exceeds SeqZero window")
                await self._network(part)
            for attempt in range(3):
                if state["cancelled"]:
                    raise MeshError("Peer cancelled segment transfer")
                if state["mask"] == state["full"]:
                    return
                state["event"].clear()
                try:
                    async with asyncio.timeout(2):
                        await state["event"].wait()
                except TimeoutError:
                    pass
                if state["mask"] == state["full"]:
                    return
                if attempt < 2:
                    for n, part in enumerate(parts):
                        if not state["mask"] & (1 << n):
                            if self.allocator.diagnostics()["next"] - first_seq >= 8192:
                                raise MeshError("Segment retransmission exceeds SeqZero window")
                            await self._network(part)
            raise MeshTimeoutError("Segment delivery not acknowledged")
        finally:
            self.tx_segments = None

    async def request(self, payload, matcher, *, device=False, retries=0, destination=None):
        # Both the key choice and request/response matching are serialized.
        async with self.request_lock:
            self.destination = destination or self.node
            if not 1 <= self.destination <= 0x7FFF or self.destination == self.source:
                raise MeshError("Commands require a distinct valid unicast destination")
            self.allowed_sources.add(self.destination)
            self.device_command = device
            try:
                return await self.channel.request(
                    payload, matcher, timeout=15, retries=retries, idempotent=bool(retries)
                )
            finally:
                self.device_command = False

    @property
    def request_lock(self):
        if not hasattr(self, "_request_lock"):
            self._request_lock = asyncio.Lock()
        return self._request_lock

    async def receive(self, kind, pdu):
        if self.closed or kind not in (0, 2):
            return
        async with self.rx_lock:
            msg = self.codec.decode(pdu, self.iv_index, proxy=kind == 2)
            if not self.network_dedup.accept(msg.iv_index, msg.src, msg.seq):
                return
            self.log(
                {
                    "kind": "mesh",
                    "src": msg.src,
                    "dst": msg.dst,
                    "seq": msg.seq,
                    "iv_index": msg.iv_index,
                    "nid": msg.nid,
                    "ctl": msg.ctl,
                    "proxy": kind == 2,
                }
            )
            if kind == 2:
                # Config responses may originate at the selected mesh proxy server.
                if not await self.replay.accept(msg.iv_index, msg.src, msg.seq):
                    return
                if len(msg.lower) == 4 and msg.lower[:2] == b"\x03\x00" and self.filter_future:
                    future, count = self.filter_future
                    if int.from_bytes(msg.lower[2:], "big") == count and not future.done():
                        future.set_result(msg.lower)
                return
            if msg.src not in self.allowed_sources or msg.dst != self.source:
                return
            if msg.ctl:
                if not await self.replay.accept(msg.iv_index, msg.src, msg.seq):
                    return
                if msg.lower and msg.lower[0] == 0:
                    zero, block, obo = decode_ack(msg.lower)
                    state = self.tx_segments
                    if (
                        state
                        and zero == state["seqzero"]
                        and msg.src == state["destination"]
                        and not obo
                        and msg.iv_index == self.iv_index
                    ):
                        if not block:
                            state["cancelled"] = True
                        state["mask"] |= block & state["full"]
                        state["event"].set()
                return
            if msg.lower and msg.lower[0] & 128 and len(msg.lower) >= 4:
                zero = ((msg.lower[1] & 127) << 6) | (msg.lower[2] >> 2)
                seq_auth = (msg.seq & ~8191) | zero
                if seq_auth > msg.seq:
                    seq_auth -= 8192
                key = msg.iv_index, msg.src, seq_auth
                if key in self.completed and msg.iv_index == self.iv_index:
                    cached_ack = self.completed[key]
                    await self._network(
                        segment_ack(cached_ack[0], cached_ack[1]), ctl=True, destination=msg.src
                    )
                    return
                if not self.replay.begin(msg.iv_index, msg.src, seq_auth):
                    return
            access = self.receiver.receive(msg)
            ack = self.receiver.ack_info
            if access is None:
                return  # Full ACK only after complete authenticated reassembly.
            assert self.receiver.last_completed is not None
            seq_auth, device = self.receiver.last_completed
            cache_key = msg.iv_index, msg.src, seq_auth
            if not await self.replay.accept(msg.iv_index, msg.src, seq_auth):
                # Re-ACK a retransmitted completed message from this live session.
                if ack and cache_key in self.completed and msg.iv_index == self.iv_index:
                    await self._network(segment_ack(ack[0], ack[1]), ctl=True, destination=msg.src)
                return
            if ack and msg.iv_index == self.iv_index:
                await self._network(segment_ack(ack[0], ack[1]), ctl=True, destination=msg.src)
                self.completed[cache_key] = ack
                if len(self.completed) > 32:
                    self.completed.popitem(last=False)
            response = Response(
                msg.src, msg.dst, msg.iv_index, seq_auth, device, decode_access(access)
            )
            self.log(
                {
                    "kind": "access",
                    "src": msg.src,
                    "dst": msg.dst,
                    "seq_auth": seq_auth,
                    "opcode": response.access.opcode,
                    "company": response.access.company,
                    "payload": response.access.payload.hex()
                    if not device
                    else "**device-key payload omitted**",
                }
            )
            await self.on_response(response)
            self.channel.receive(response)

    def close(self):
        self.closed = True
        if self.channel.pending:
            future = self.channel.pending[1]
            if not future.done():
                future.set_exception(MeshError("Connection closed"))
        if self.filter_future and not self.filter_future[0].done():
            self.filter_future[0].set_exception(MeshError("Connection closed"))
