import asyncio
import copy
import pytest
from custom_components.flinq_orion.protocol.vendor_tuya import (
    DP,
    encode_dp,
    decode_dps,
    query,
    decode_report,
)
from custom_components.flinq_orion.protocol.conversions import (
    brightness_to_ha,
    brightness_to_tuya,
    encode_hsv_string,
    decode_hsv_string,
)
from custom_components.flinq_orion.protocol.crypto import k2, k4
from custom_components.flinq_orion.protocol.network import NetworkCodec, NetworkMessage
from custom_components.flinq_orion.protocol.sequence import SequenceAllocator
from custom_components.flinq_orion.protocol.proxy import ProxySAR
from custom_components.flinq_orion.protocol.transport import ReplayWindow
from custom_components.flinq_orion.protocol.access import decode_access
from custom_components.flinq_orion.protocol.errors import (
    MeshError,
    MeshDecryptionError,
    TuyaDPError,
)
from custom_components.flinq_orion.device_profiles import DeviceCapabilities


@pytest.mark.parametrize(
    "dp",
    [
        DP(1, 1, b"\x01"),
        DP(54, 2, b"\x00\x00\x00\x1e"),
        DP(5, 3, b"007803e803e8"),
        DP(6, 0, b"\xff\x00"),
        DP(2, 4, b"\x02"),
        DP(59, 5, b"\x01\x02"),
    ],
)
def test_dp_roundtrip(dp):
    assert decode_dps(encode_dp(dp)) == [dp]


def test_compact_bool_has_no_length():
    assert encode_dp(DP(1, 1, b"\x01")).hex() == "010101"
    assert query().hex() == "ccd007010100"


@pytest.mark.parametrize(
    "raw", [b"\x01", b"\x01\x01", b"\x01\x01\x02", b"\x01\x02\x00", b"\x01\xff", b"\x01\x03\x08abc"]
)
def test_malformed(raw):
    with pytest.raises(TuyaDPError):
        decode_dps(raw)


def test_unknown_dp():
    c = DeviceCapabilities()
    # DP 57 is not in the Orion schema; DP 54 (pir_sensitivity) is.
    unknown, known = decode_report(bytes.fromhex("0139020000001e" + "360401"))
    c.observe(unknown)
    c.observe(known)
    assert c.unknown_dps == {"57": {"type": 2, "raw": "0000001e", "value": 30}}
    assert c.known_dps == {"54": {"type": 4, "raw": "01", "value": 1}}


def test_brightness():
    assert brightness_to_ha(750) == 191
    assert brightness_to_ha(1000) == 255
    assert brightness_to_tuya(255) == 1000
    assert brightness_to_tuya(0) == 10
    assert all(brightness_to_ha(i) <= brightness_to_ha(i + 1) for i in range(1000))


def test_color_candidate():
    assert decode_hsv_string(encode_hsv_string(120, 100, 100)) == (120.0, 100.0, 100.0)
    with pytest.raises(ValueError):
        decode_hsv_string("ffff03e803e8")


def test_sig_key_vectors():
    nid, encryption, privacy = k2(bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6"))
    assert nid == 0x68
    assert encryption.hex() == "0953fa93e7caac9638f58820220a398e"
    assert privacy.hex() == "8b84eedec100067d670971dd2aa700cf"
    assert k4(bytes.fromhex("3216d1509884b533248541792b877f98")) == 0x38


def test_network_roundtrip_and_mic():
    codec = NetworkCodec(bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6"))
    msg = NetworkMessage(codec.nid, 0, False, 5, 1, 0xB0, 1, b"\x78abcdef")
    pdu = codec.encode(msg)
    assert codec.decode(pdu, 0) == msg
    with pytest.raises(MeshDecryptionError):
        codec.decode(pdu[:-1] + bytes((pdu[-1] ^ 1,)), 0)


def test_opcode():
    p = decode_access(bytes.fromhex("cdd00701010101"))
    assert (p.opcode, p.company, p.payload) == (0xCD, 0x7D0, bytes.fromhex("01010101"))


def test_dedup():
    w = ReplayWindow()
    assert w.accept(0, 176, 1)
    assert not w.accept(0, 176, 1)
    assert w.accept(1, 176, 1)


def test_proxy_sar():
    s = ProxySAR()
    assert s.feed(b"\x40abc") is None
    assert s.feed(b"\x80def") is None
    assert s.feed(b"\xc0ghi") == (0, b"abcdefghi")
    with pytest.raises(MeshError):
        s.feed(b"\x80x")


class Store:
    def __init__(self):
        self.data = None
        self.fail = False

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        if self.fail:
            raise OSError("disk failed")
        self.data = copy.deepcopy(data)


@pytest.mark.asyncio
async def test_reservation_crash_and_parallel():
    store = Store()
    a = SequenceAllocator(store, "new-source", 0, 16)
    await a.load()
    values = await asyncio.gather(*(a.next() for _ in range(40)))
    assert values == list(range(40))
    assert store.data["reserved_until"] == 48
    restarted = SequenceAllocator(store, "new-source", 0, 16)
    await restarted.load()
    assert await restarted.next() == 48


@pytest.mark.asyncio
async def test_failed_save_stops_tx():
    store = Store()
    store.fail = True
    a = SequenceAllocator(store, "new-source", 0)
    await a.load()
    with pytest.raises(OSError):
        await a.next()
    store.fail = False
    with pytest.raises(MeshError):
        await a.next()


@pytest.mark.asyncio
async def test_iv_mismatch():
    store = Store()
    a = SequenceAllocator(store, "source", 0)
    await a.load()
    await a.next()
    with pytest.raises(MeshError):
        await SequenceAllocator(store, "source", 1).load()


def test_composition():
    from custom_components.flinq_orion.protocol.models import decode_composition

    raw = bytes.fromhex("00d0070100010010000100000001010010d0070400")
    result = decode_composition(raw)
    assert result.elements[0].sig_models == (0x1000,)
    assert result.elements[0].vendor_models == ((0x07D0, 4),)
    with pytest.raises(MeshError):
        decode_composition(raw[:-1])


@pytest.mark.asyncio
async def test_command_matching_and_serialization():
    from custom_components.flinq_orion.protocol.commands import CommandChannel

    sent = []

    async def send(payload):
        sent.append(payload)
        assert not channel.receive("unrelated")
        channel.receive(payload)

    channel = CommandChannel(send)
    result = await asyncio.gather(
        *(
            channel.request(bytes([n]), lambda response, n=n: response == bytes([n]))
            for n in range(3)
        )
    )
    assert result == sent == [b"\x00", b"\x01", b"\x02"]


@pytest.mark.asyncio
async def test_command_timeout_cleanup():
    from custom_components.flinq_orion.protocol.commands import CommandChannel
    from custom_components.flinq_orion.protocol.errors import MeshTimeoutError

    async def send(payload):
        pass

    channel = CommandChannel(send)
    with pytest.raises(MeshTimeoutError):
        await channel.request(b"query", lambda response: True, timeout=0.001)
    assert channel.pending is None
    with pytest.raises(ValueError):
        await channel.request(b"write", lambda response: True, retries=1)


def test_segmented_access_receive_out_of_order():
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    from custom_components.flinq_orion.protocol.transport import AccessReceiver

    key = bytes.fromhex("3216d1509884b533248541792b877f98")
    receiver = AccessReceiver(key)
    seq, src, dst, iv = 20, 176, 1, 0
    plain = bytes.fromhex("cdd00701") + encode_dp(DP(6, 3, b"example-long-scene"))
    nonce = (
        bytes((1, 0))
        + seq.to_bytes(3, "big")
        + src.to_bytes(2, "big")
        + dst.to_bytes(2, "big")
        + iv.to_bytes(4, "big")
    )
    encrypted = AESCCM(key, tag_length=4).encrypt(nonce, plain, None)
    parts = [encrypted[i : i + 12] for i in range(0, len(encrypted), 12)]
    messages = []
    for n, part in enumerate(parts):
        header = bytes(
            (
                0xC0 | receiver.aid,
                seq >> 6,
                ((seq & 63) << 2) | (n >> 3),
                ((n & 7) << 5) | (len(parts) - 1),
            )
        )
        messages.append(NetworkMessage(0, iv, False, 5, seq + n, src, dst, header + part))
    assert receiver.receive(messages[-1]) is None
    for msg in messages[:-2]:
        assert receiver.receive(msg) is None
    assert receiver.receive(messages[-2]) == plain


def test_mesh_advertisement_classification():
    from custom_components.flinq_orion.protocol.advertisements import classify, PROVISIONING, PROXY

    assert classify({PROXY: b"\x00" + bytes(8)})[0]["format"] == "network_identity"
    assert classify({PROXY: b"\x01" + bytes(16)})[0]["format"] == "node_identity"
    assert classify({PROVISIONING: bytes(18)})[0]["format"] == "standard_unprovisioned_device"
    assert classify({PROXY: b"\xff"})[0]["format"] == "unknown"
