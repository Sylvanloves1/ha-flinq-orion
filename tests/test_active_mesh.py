"""Encrypted virtual peer exercises full request/ACK/report path without radio."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from custom_components.flinq_orion.protocol.network import NetworkCodec, NetworkMessage
from custom_components.flinq_orion.protocol.transport import AccessReceiver
from custom_components.flinq_orion.protocol.transmit import (
    encode_upper,
    lower_segments,
    segment_ack,
    decode_ack,
)
from custom_components.flinq_orion.protocol.controller import MeshController, REJECT_LIST
from custom_components.flinq_orion.protocol.sequence import SequenceAllocator
from custom_components.flinq_orion.protocol.replay import ReplayProtection
from custom_components.flinq_orion.protocol.access import decode_access
from custom_components.flinq_orion.protocol.vendor_tuya import (
    DP,
    encode_dp,
    decode_dps,
    DATA,
    READ,
    WRITE,
    opcode,
    query,
)
from custom_components.flinq_orion.protocol.errors import MeshError, MeshTimeoutError, TuyaDPError
from custom_components.flinq_orion.device import OrionDevice
from custom_components.flinq_orion.device_profiles import DeviceCapabilities

NET = bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6")
APP = bytes.fromhex("3216d1509884b533248541792b877f98")
DEV = bytes.fromhex("9d6dd0e96eb25dc19a40ed9914f8f03f")


class MemoryStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        self.data = copy.deepcopy(data)


class Peer:
    def __init__(self):
        self.codec = NetworkCodec(NET)
        self.receiver = AccessReceiver(APP, DEV)
        self.seq = 0
        self.tasks = []
        self.received = []
        self.acks = []
        self.drop_segments = set()
        self.responses = []
        self.dps = {
            1: DP(1, 1, b"\x00"),
            3: DP(3, 2, (750).to_bytes(4, "big")),
            54: DP(54, 4, b"\x00"),
        }
        self.filter_type = 0
        # None: report to the requester. A group address: publish the report there and
        # only acknowledge the requester with an empty DP report, as the Orion does.
        self.report_destination = None
        self.acknowledge_requester = True
        self.address = 176

    def message(self, lower, *, ctl=False, proxy=False, destination=1):
        m = NetworkMessage(
            self.codec.nid,
            0,
            ctl,
            0 if proxy else 5,
            self.seq,
            self.address,
            0 if proxy else destination,
            lower,
        )
        self.seq += 1
        return self.codec.encode(m, proxy=proxy)

    def inject(self, kind, pdu):
        self.responses.append((kind, pdu))
        self.tasks.append(asyncio.create_task(self.controller.receive(kind, pdu)))

    async def send(self, kind, pdu):
        msg = self.codec.decode(pdu, 0, proxy=kind == 2)
        self.received.append(msg)
        if kind == 2:
            if msg.lower[0] == 0:
                self.filter_type, count = msg.lower[1], 0
            else:
                count = 1
            status = bytes((3, self.filter_type, 0, count))
            self.inject(2, self.message(status, ctl=True, proxy=True))
            return
        if msg.ctl:
            self.acks.append(decode_ack(msg.lower))
            return
        if msg.lower[0] & 128:
            n = ((msg.lower[2] & 3) << 3) | (msg.lower[3] >> 5)
            if n in self.drop_segments:
                self.drop_segments.remove(n)
                return
        access = self.receiver.receive(msg)
        if access is None:
            return
        if self.receiver.ack_info:
            zero, block, _ = self.receiver.ack_info
            self.inject(0, self.message(segment_ack(zero, block), ctl=True))
        request = decode_access(access)
        if request.company == 0x7D0:
            if request.opcode == WRITE:
                for dp in decode_dps(request.payload[1:]):
                    self.dps[dp.id] = dp
            if request.opcode in (READ, WRITE):
                payload = (
                    opcode(DATA) + b"\x01" + b"".join(encode_dp(dp) for dp in self.dps.values())
                )
                if self.report_destination is None:
                    await self.report(payload)
                else:
                    await self.report(payload, self.report_destination)
                    if self.acknowledge_requester:
                        await self.report(opcode(DATA) + b"\x01")

    async def report(self, payload, destination=1):
        first = self.seq
        encrypted = encode_upper(payload, APP, first, self.address, destination, 0)
        for lower in lower_segments(encrypted, APP, first):
            self.inject(0, self.message(lower, destination=destination))

    async def finish(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)


async def make_system(options=None):
    peer = Peer()
    allocator = SequenceAllocator(MemoryStore(), "synthetic-controller", 0)
    await allocator.load()
    replay = ReplayProtection(MemoryStore())
    await replay.load()
    c = SimpleNamespace(
        entry=SimpleNamespace(
            options={"transport_mode": "vendor_dp", "node_address": 176, **(options or {})}
        ),
        capabilities=DeviceCapabilities(),
        data={},
        record=Mock(),
        report_received=Mock(),
        async_set_updated_data=Mock(),
    )
    device = OrionDevice(c)
    controller = MeshController(
        peer.codec, APP, DEV, 1, 176, 0, allocator, replay, peer.send, device.response, Mock()
    )
    peer.controller = controller
    device.controller = controller
    device.ensure_connection = AsyncMock()
    return peer, controller, device, allocator, replay


@pytest.mark.asyncio
async def test_proxy_filter_and_vendor_power_no_optimistic_state():
    peer, controller, device, allocator, replay = await make_system()
    assert not device.supports_power
    await controller.configure_filter()
    await device.query()
    assert device.supports_power and device.supports_brightness
    assert device.dp(1)["value"] is False
    assert peer.filter_type == REJECT_LIST  # accept every destination on this link
    assert device.setting(54) == "low"
    await device.set_power(True)
    assert device.dp(1)["value"] is True
    await peer.finish()
    assert peer.acks  # Report required receive segmentation and a transport ACK.
    seqs = [m.seq for m in peer.received]
    assert seqs == sorted(set(seqs))
    assert replay.entries["176"][1] > 0


@pytest.mark.asyncio
async def test_segmented_color_with_missing_segment_retransmission():
    peer, controller, device, allocator, replay = await make_system(
        {"hsv_encoding_confirmed": True}
    )
    peer.dps[5] = DP(5, 3, b"000003e803e8")
    await device.query()
    peer.drop_segments = {1}
    await device.set_color((120, 100), 255)
    assert device.dp(5)["value"] == "007803e803e8"
    await peer.finish()
    outbound = [m for m in peer.received if not m.ctl and m.lower[0] & 128]
    assert len(outbound) >= 4  # Initial segments and missing-segment retry.
    assert len({m.seq for m in outbound}) == len(outbound)


@pytest.mark.asyncio
async def test_replayed_report_does_not_restore_old_power():
    peer, controller, device, allocator, replay = await make_system()
    await device.query()
    old = list(peer.responses)
    await device.set_power(True)
    transmitted = len(peer.received)
    for kind, pdu in old:
        await controller.receive(kind, pdu)
    assert len(peer.received) == transmitted
    assert device.dp(1)["value"] is True
    await peer.finish()


@pytest.mark.asyncio
async def test_settings_are_written_only_within_the_schema():
    peer, controller, device, allocator, replay = await make_system()
    await device.query()
    with pytest.raises(MeshError):
        await device.write_dp(DP(54, 4, b"\x02"))  # light DPs only
    await device.write_setting(54, "high")
    assert peer.dps[54] == DP(54, 4, b"\x02") and device.setting(54) == "high"
    for identifier, value, error in (
        (54, "loud", TuyaDPError),
        (55, 4, TuyaDPError),
        (55, True, TuyaDPError),
        (52, "pir", MeshError),
        (99, 1, MeshError),
    ):
        with pytest.raises(error):
            await device.write_setting(identifier, value)
    assert peer.dps[54] == DP(54, 4, b"\x02") and 55 not in peer.dps
    await peer.finish()


@pytest.mark.asyncio
async def test_reports_published_to_a_group_are_read_but_not_acknowledged():
    peer, controller, device, allocator, replay = await make_system()
    peer.report_destination = 0xD000
    peer.dps[56] = DP(56, 1, b"\x00")
    await device.query()
    assert device.setting(56) is False and device.dp(1)["value"] is False
    await device.write_setting(56, True)
    assert device.setting(56) is True
    await peer.finish()
    assert peer.acks == []  # segmented group reports are never acknowledged


@pytest.mark.asyncio
async def test_interleaved_control_does_not_discard_started_segmentation():
    store = MemoryStore()
    rpl = ReplayProtection(store)
    await rpl.load()
    assert rpl.begin(0, 176, 10)
    assert await rpl.accept(0, 176, 12)
    assert await rpl.accept(0, 176, 10)
    assert not await rpl.accept(0, 176, 10)
    restarted = ReplayProtection(store)
    await restarted.load()
    assert not restarted.begin(0, 176, 10)


def test_official_proxy_configuration_vector():
    codec = NetworkCodec(bytes.fromhex("d1aafb2a1a3c281cbdb0e960edfad852"))
    msg = NetworkMessage(0x10, 0x12345678, True, 0, 1, 1, 0, b"\x00\x00")
    packet = codec.encode(msg, proxy=True)
    assert packet.hex() == "10386bd60efbbb8b8c28512e792d3711f4b526"
    assert codec.decode(packet, msg.iv_index, proxy=True) == msg
    with pytest.raises(MeshError):
        codec.decode(packet, msg.iv_index)


@pytest.mark.asyncio
async def test_device_key_transport():
    codec = NetworkCodec(NET)
    receiver = AccessReceiver(APP, DEV)
    payload = bytes.fromhex("800800")
    encrypted = encode_upper(payload, DEV, 5, 1, 176, 0, device=True)
    lower = lower_segments(encrypted, DEV, 5, device=True)[0]
    assert receiver.receive(NetworkMessage(codec.nid, 0, False, 5, 5, 1, 176, lower)) == payload
    assert receiver.last_completed == (5, True)


@pytest.mark.asyncio
async def test_empty_vendor_report_is_a_response_not_a_timeout():
    peer, controller, device, allocator, replay = await make_system()
    peer.dps = {}
    response = await device.query()
    assert response.access.payload == b"\x01"
    assert len([msg for msg in peer.received if not msg.ctl]) == 1
    assert not device.supports_power
    assert device.coordinator.record.call_args.args[0]["result"] == "empty_dp_report"
    await peer.finish()


@pytest.mark.asyncio
async def test_reply_to_another_controller_updates_state_but_does_not_answer_us():
    peer, controller, device, allocator, replay = await make_system()
    peer.report_destination = 0x0005
    peer.acknowledge_requester = False
    controller.requested = True  # not the first request of the link: keep the short timeout
    with pytest.raises(MeshTimeoutError):
        await controller.request(query((0,)), lambda response: True, timeout=0.3)
    await peer.finish()
    assert device.dp(1)["value"] is False  # read from the reply to 0x0005
    assert peer.acks == []


@pytest.mark.asyncio
async def test_first_request_of_a_link_waits_longer():
    peer, controller, device, allocator, replay = await make_system()
    controller.channel.request = AsyncMock(return_value="response")
    await controller.request(b"\x82\x01", lambda response: True, timeout=2)
    await controller.request(b"\x82\x01", lambda response: True, timeout=2)
    timeouts = [call.kwargs["timeout"] for call in controller.channel.request.await_args_list]
    assert timeouts == [5, 2]
