"""Pairing against a synthetic Orion: PB-GATT provisionee, then proxy and config server."""

import os
from unittest.mock import AsyncMock, patch
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from homeassistant.core import HomeAssistant
from custom_components.flinq_orion import pairing
from custom_components.flinq_orion.const import PROXY_IN
from custom_components.flinq_orion.protocol import provisioning as prov
from custom_components.flinq_orion.protocol.access import decode_access
from custom_components.flinq_orion.protocol.crypto import cmac, hmac_sha256, k1, k5, s1, s2
from custom_components.flinq_orion.protocol.proxy import ProxySAR, segments
from custom_components.flinq_orion.protocol.transmit import (
    encode_upper,
    lower_segments,
    segment_ack,
)
from custom_components.flinq_orion.protocol.transport import AccessReceiver
from test_active_mesh import APP, NET, Peer

ADDRESS = "DC:23:4F:00:00:02"
# Orion composition (device diagnostics of 2026-10-14): one element.
ORION_SIG_MODELS = (
    0x0000,
    0x0002,
    0x0003,
    0x1203,
    0x1204,
    0x1000,
    0x1300,
    0x1301,
    0x1303,
    0x1304,
    0x1306,
    0x1307,
    0x1308,
    0x130A,
    0x130B,
)


def composition_page() -> bytes:
    header = b"".join(n.to_bytes(2, "little") for n in (0x07D0, 0x0300, 0x3135, 105, 7))
    element = bytes(2) + bytes((len(ORION_SIG_MODELS), 1))
    element += b"".join(m.to_bytes(2, "little") for m in ORION_SIG_MODELS)
    element += (0x07D0).to_bytes(2, "little") + (4).to_bytes(2, "little")
    return b"\x00" + header + element


class Provisionee:
    """Device side of the Provisioning protocol (Mesh 5.4), No OOB."""

    def __init__(self, algorithms=0x0001, oob=0, ignore_invites=0, fail_start=False):
        self.algorithms, self.oob = algorithms, oob
        self.ignore_invites, self.fail_start = ignore_invites, fail_start
        self.wrong_confirmation = False
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = prov.public_key_bytes(self.private.public_key())
        self.received: list[int] = []
        self.provisioned: dict | None = None
        self.stopped = False

    def confirm(self, random):
        if self.algorithm == prov.HMAC_SHA256:
            return hmac_sha256(self.key, random)
        return cmac(self.key, random + bytes(16))

    def handle(self, data: bytes) -> list[bytes]:
        kind, params = data[0], data[1:]
        self.received.append(kind)
        if self.stopped:
            return []  # a device that sent Failed ends the procedure
        if kind == prov.INVITE:
            if self.ignore_invites:
                self.ignore_invites -= 1
                return []
            caps = bytes((1,)) + self.algorithms.to_bytes(2, "big") + bytes((0, self.oob))
            caps += bytes(6)
            self.inputs = params + caps
            return [prov.pdu(prov.CAPABILITIES, caps)]
        if kind == prov.START:
            if self.fail_start:
                self.stopped = True
                return [prov.pdu(prov.FAILED, b"\x02")]
            self.algorithm = params[0]
            self.inputs += params
            return []
        if kind == prov.PUBLIC_KEY:
            peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + params)
            self.ecdh = self.private.exchange(ec.ECDH(), peer)
            inputs = self.inputs + params + self.public
            if self.algorithm == prov.HMAC_SHA256:
                self.salt = s2(inputs)
                self.key = k5(self.ecdh + bytes(32), self.salt, b"prck256")
            else:
                self.salt = s1(inputs)
                self.key = k1(self.ecdh, self.salt, b"prck")
            return [prov.pdu(prov.PUBLIC_KEY, self.public)]
        if kind == prov.CONFIRMATION:
            self.provisioner_confirmation = params
            self.random = os.urandom(len(params))
            confirmation = self.confirm(self.random)
            if self.wrong_confirmation:
                confirmation = bytes(len(confirmation))
            return [prov.pdu(prov.CONFIRMATION, confirmation)]
        if kind == prov.RANDOM:
            if self.confirm(params) != self.provisioner_confirmation:
                return [prov.pdu(prov.FAILED, b"\x04")]
            self.provisioner_random = params
            return [prov.pdu(prov.RANDOM, self.random)]
        if kind == prov.DATA:
            salt = s1(self.salt + self.provisioner_random + self.random)
            session_key = k1(self.ecdh, salt, b"prsk")
            nonce = k1(self.ecdh, salt, b"prsn")[3:]
            data = AESCCM(session_key, tag_length=8).decrypt(nonce, params, None)
            self.provisioned = {
                "net_key": data[:16],
                "iv_index": int.from_bytes(data[19:23], "big"),
                "unicast": int.from_bytes(data[23:25], "big"),
                "device_key": k1(self.ecdh, salt, b"prdk"),
            }
            return [prov.pdu(prov.COMPLETE)]
        return [prov.pdu(prov.FAILED, b"\x03")]


class ConfigPeer(Peer):
    """The paired lamp as mesh node: proxy filter, Segment ACKs and config server."""

    def __init__(self, unicast, device_key, appkey_status=0):
        super().__init__()
        self.address = unicast
        self.device_key = device_key
        self.receiver = AccessReceiver(APP, device_key)
        self.appkey_status = appkey_status
        self.config: list[int] = []

    async def send(self, kind, pdu):
        msg = self.codec.decode(pdu, 0, proxy=kind == 2)
        if kind == 2 or msg.ctl:
            return await super().send(kind, pdu)
        access = self.receiver.receive(msg)
        if access is None:
            return
        if self.receiver.ack_info:
            zero, block, _ = self.receiver.ack_info
            self.inject(0, self.message(segment_ack(zero, block), ctl=True))
        request = decode_access(access)
        self.config.append(request.opcode)
        if request.opcode == 0x00:
            reply = bytes.fromhex("8003") + bytes((self.appkey_status,)) + request.payload[:3]
        elif request.opcode == 0x8008:
            reply = b"\x02" + composition_page()
        elif request.opcode == 0x803D:
            reply = bytes.fromhex("803e") + b"\x00" + request.payload
        elif request.opcode == 0x03:
            reply = bytes.fromhex("8019") + b"\x00" + request.payload
        else:
            return
        first = self.seq
        encrypted = encode_upper(reply, self.device_key, first, self.address, 1, 0, device=True)
        for lower in lower_segments(encrypted, self.device_key, first, device=True):
            self.inject(0, self.message(lower, destination=1))


class Lamp:
    def __init__(self, provisionee, appkey_status=0):
        self.provisionee = provisionee
        self.appkey_status = appkey_status
        self.peer: ConfigPeer | None = None
        self.client = None

    def mesh(self):
        if self.peer is None:
            state = self.provisionee.provisioned
            assert state and state["net_key"] == NET
            self.peer = ConfigPeer(state["unicast"], state["device_key"], self.appkey_status)
        self.peer.inject = self.client.inject
        return self.peer


class Client:
    """Fake GATT client; the same characteristic handles before and after pairing."""

    def __init__(self, lamp, answer_proxy=True):
        self.lamp, self.answer_proxy = lamp, answer_proxy
        self.is_connected = True
        self.sar = ProxySAR()
        self.cleared = False
        self.callback = None

    async def start_notify(self, uuid, callback):
        self.callback = callback
        self.lamp.client = self

    def inject(self, kind, pdu):
        for segment in segments(kind, pdu):
            self.callback(1, segment)

    async def write_gatt_char(self, uuid, data, response):
        assert response is False and len(data) <= 20
        result = self.sar.feed(data)
        if not result:
            return
        kind, payload = result
        if uuid == pairing.PROVISIONING_IN:
            for reply in self.lamp.provisionee.handle(payload):
                self.inject(3, reply)
        elif uuid == PROXY_IN and self.answer_proxy:
            await self.lamp.mesh().send(kind, payload)

    async def clear_cache(self):
        self.cleared = True
        return True

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(pairing, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(pairing, "INVITE_TIMEOUT", 0.2)
    monkeypatch.setattr(pairing, "STEP_TIMEOUT", 2)
    monkeypatch.setattr(pairing, "FILTER_TIMEOUT", 0.3)


async def run(tmp_path, lamp, *clients):
    hass = HomeAssistant(str(tmp_path))
    network = await pairing.new_network(hass, NET, APP)
    connector = AsyncMock(side_effect=list(clients))
    with (
        patch(
            "custom_components.flinq_orion.pairing.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch("custom_components.flinq_orion.pairing.establish_connection", connector),
    ):
        outcome = await pairing.pair(hass, network, ADDRESS)
    return outcome, network, connector


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithms", [0x0001, 0x0003])
async def test_pairing_and_configuration_on_the_same_link(tmp_path, algorithms):
    lamp = Lamp(Provisionee(algorithms=algorithms))
    client = Client(lamp)
    outcome, network, connector = await run(tmp_path, lamp, client)
    state = lamp.provisionee.provisioned
    assert outcome.configured and outcome.unicast == 0x10 == state["unicast"]
    assert outcome.device_key == state["device_key"].hex()
    assert connector.await_count == 1
    assert connector.call_args.kwargs["use_services_cache"] is False
    # AppKey Add comes first (Tuya: configuration within 30 s); then composition.
    assert lamp.peer.config[:2] == [0x00, 0x8008]
    binds = [name for name in outcome.results if name.startswith("bind_")]
    # Light models and the vendor model first, then the other non-foundation models.
    assert binds[:6] == [
        "bind_0010_1000",
        "bind_0010_1300",
        "bind_0010_1303",
        "bind_0010_1306",
        "bind_0010_1307",
        "bind_0010_07d0_0004",
    ]
    assert len(binds) == 13
    assert all(outcome.results[name] == 0 for name in binds)
    assert [n for n in outcome.results if n.startswith("publish_")] == [
        "publish_0010_1000",
        "publish_0010_1300",
        "publish_0010_1303",
        "publish_0010_1307",
    ]
    assert network.store.allocated == [[0x10, 1]]
    assert network.store.pending[0]["unicast"] == 0x10
    # The proxy keeps the provisioning table: cleared before the next normal link.
    assert client.cleared and not client.is_connected


@pytest.mark.asyncio
async def test_a_first_invite_that_is_ignored_is_sent_again(tmp_path):
    lamp = Lamp(Provisionee(ignore_invites=1))
    outcome, _, _ = await run(tmp_path, lamp, Client(lamp))
    assert outcome.configured
    assert lamp.provisionee.received[:2] == [prov.INVITE, prov.INVITE]


@pytest.mark.asyncio
async def test_without_a_proxy_answer_the_link_is_opened_again(tmp_path):
    lamp = Lamp(Provisionee())
    first, second = Client(lamp, answer_proxy=False), Client(lamp)
    outcome, _, connector = await run(tmp_path, lamp, first, second)
    assert outcome.configured and connector.await_count == 2
    assert first.cleared and not first.is_connected


@pytest.mark.asyncio
async def test_configuration_failure_keeps_the_paired_lamp(tmp_path):
    lamp = Lamp(Provisionee(), appkey_status=0x05)
    outcome, network, _ = await run(tmp_path, lamp, Client(lamp))
    assert not outcome.configured and outcome.results["error"] == "MeshError"
    assert outcome.device_key == lamp.provisionee.provisioned["device_key"].hex()
    assert network.store.pending[0]["address"] == ADDRESS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provisionee", "reason"),
    [
        (Provisionee(fail_start=True), "invalid_format"),
        (Provisionee(oob=0x02), "oob_required"),
        (Provisionee(ignore_invites=3), "timeout"),
    ],
)
async def test_pairing_errors_before_complete(tmp_path, provisionee, reason):
    lamp = Lamp(provisionee)
    client = Client(lamp)
    with pytest.raises(prov.ProvisioningError) as err:
        await run(tmp_path, lamp, client)
    assert err.value.reason == reason
    assert not client.is_connected


@pytest.mark.asyncio
async def test_a_wrong_device_confirmation_stops_before_data(tmp_path):
    provisionee = Provisionee()
    provisionee.wrong_confirmation = True
    lamp = Lamp(provisionee)
    hass = HomeAssistant(str(tmp_path))
    network = await pairing.new_network(hass, NET, APP)
    with (
        patch(
            "custom_components.flinq_orion.pairing.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch(
            "custom_components.flinq_orion.pairing.establish_connection",
            AsyncMock(return_value=Client(lamp)),
        ),
    ):
        with pytest.raises(prov.ProvisioningError) as err:
            await pairing.pair(hass, network, ADDRESS)
    assert err.value.reason == "confirmation_failed"
    assert prov.DATA not in provisionee.received and network.store.allocated == []
