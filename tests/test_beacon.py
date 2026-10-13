import pytest
from custom_components.flinq_orion.protocol.beacon import decode_secure_beacon
from custom_components.flinq_orion.protocol.crypto import k1, k3, s1, cmac
from custom_components.flinq_orion.protocol.errors import MeshDecryptionError


def test_secure_beacon_and_mic():
    key = bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6")
    assert k3(key).hex() == "3ecaff672f673370"
    fields = b"\x02" + k3(key) + (1).to_bytes(4, "big")
    auth = cmac(k1(key, s1(b"nkbk"), b"id128\x01"), fields)[:8]
    packet = b"\x01" + fields + auth
    beacon = decode_secure_beacon(packet, key)
    assert beacon.iv_index == 1 and beacon.iv_update and not beacon.key_refresh
    with pytest.raises(MeshDecryptionError):
        decode_secure_beacon(packet[:-1] + bytes((packet[-1] ^ 1,)), key)
