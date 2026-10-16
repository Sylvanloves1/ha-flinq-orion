"""Provisioning against the Mesh Protocol 1.1 sample data (sections 8.7 and 8.8)."""

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from custom_components.flinq_orion.protocol import provisioning as prov, sig
from custom_components.flinq_orion.protocol.advertisements import PROVISIONING, PROXY
from custom_components.flinq_orion.protocol.advertisements import unprovisioned_device
from custom_components.flinq_orion.protocol.crypto import k1, k5, s1, s2, hmac_sha256
from custom_components.flinq_orion.protocol.proxy import segments
from custom_components.flinq_orion.pairing import allocate_unicast, pairable
from types import SimpleNamespace

x = bytes.fromhex
PROVISIONER_PRIVATE = "06a516693c9aa31a6084545d0c5db641b48572b97203ddffb7ac73f7d0457663"
PROVISIONER_PUBLIC = x(
    "2c31a47b5779809ef44cb5eaaf5c3e43d5f8faad4a8794cb987e9b03745c78dd"
    "919512183898dfbecd52e2408e43871fd021109117bd3ed4eaf8437743715d4f"
)
DEVICE_PUBLIC = x(
    "f465e43ff23d3f1b9dc7dfc04da8758184dbc966204796eccf0d6cf5e16500cc"
    "0201d048bcbbd899eeefc424164e33c201c2b010ca6b4d43a8a155cad8ecb279"
)
ECDH = x("ab85843a2f6d883f62e5684b38e307335fe6e1945ecd19604105c6f23221eb69")


def sample_provisioner(random):
    key = ec.derive_private_key(int(PROVISIONER_PRIVATE, 16), ec.SECP256R1())
    return prov.Provisioner(
        x("efb2255e6422d330088e09bb015ed707"),
        0x01020304,
        key_index=0x0567,
        attention=0,
        private_key=key,
        random=lambda size: random,
    )


def test_cmac_no_oob_sample_data():
    p = sample_provisioner(x("8b19ac31d58b124c946209b5db1021b9"))
    assert p.invite() == x("0000")
    assert p.start(x("0100010000000000000000")) == x("020000000000")
    assert p.public_key() == b"\x03" + PROVISIONER_PUBLIC
    assert p.confirmation(DEVICE_PUBLIC) == x("05b38a114dfdca1fe153bd2c1e0dc46ac2")
    assert p.ecdh == ECDH
    assert p.salt == x("5faabe187337c71cc6c973369dcaa79a")
    assert p.confirmation_key == x("e31fe046c68ec339c425fc6629f0336f")
    assert p.random_pdu(x("eeba521c196b52cc2e37aa40329f554e")) == x(
        "068b19ac31d58b124c946209b5db1021b9"
    )
    p.verify(x("55a2a2bca04cd32ff6f346bd0a0c1a3a"))
    data = p.data(0x0B0C)
    assert data == x("07d0bd7f4a89a2ff6222af59a90a60ad58acfe3123356f5cec2973e0ec50783b10c7")
    assert p.device_key == x("0520adad5e0142aa3e325087b4ec16d8")


def test_wrong_device_confirmation_is_rejected():
    p = sample_provisioner(x("8b19ac31d58b124c946209b5db1021b9"))
    p.invite()
    p.start(x("0100010000000000000000"))
    p.confirmation(DEVICE_PUBLIC)
    p.random_pdu(bytes(16))
    with pytest.raises(prov.ProvisioningError) as err:
        p.verify(x("55a2a2bca04cd32ff6f346bd0a0c1a3a"))
    assert err.value.reason == "confirmation_failed"
    with pytest.raises(prov.ProvisioningError):
        p.data(0x0B0C)


def test_hmac_functions_match_the_epa_sample_data():
    # Static OOB sample of the HMAC algorithm (Mesh 1.1 EPA, in Telink's tests).
    auth = x("906d73a3c7a7cb3ff730dca68a46b9c18d673f50e078202311473ebbe253669f")
    inputs = x("0001000300010000000000000100010000") + PROVISIONER_PUBLIC + DEVICE_PUBLIC
    salt = s2(inputs)
    assert salt == x("a71141ba8cb6b40f4f52b622e1c091614c73fc308f871b78ca775e769bc3ae69")
    key = k5(ECDH + auth, salt, b"prck256")
    assert key == x("210c3c448152e8d59ef742aa7d22ee5ba59a38648bda6bf05c74f3e46fc2c0bb")
    random_provisioner = x("36f968b94a13000e64b223576390db6bcc6d62f02617c369ee3f5b3e89df7e1f")
    random_device = x("5b9b1fc6a64b2de8bece53187ee989c6566db1fc7dc8580a73dafdd6211d56a5")
    assert hmac_sha256(key, random_provisioner) == x(
        "c99b54617ae646f5f32cf7e1ea6fcc49fd69066078eba9580fa6c7031833e6c8"
    )
    provisioning_salt = s1(salt + random_provisioner + random_device)
    assert provisioning_salt == x("d1cb10ad8d51286067e348fc4b692122")
    assert k1(ECDH, provisioning_salt, b"prsk") == x("df4a494da3d45405e402f1d6a6cea338")
    assert k1(ECDH, provisioning_salt, b"prdk") == x("2770852a737cf05d8813768f22af3a2d")


def test_pb_gatt_segmentation_sample():
    assert segments(3, b"\x03" + PROVISIONER_PUBLIC) == [
        x("43032c31a47b5779809ef44cb5eaaf5c3e43d5f8"),
        x("83faad4a8794cb987e9b03745c78dd9195121838"),
        x("8398dfbecd52e2408e43871fd021109117bd3ed4"),
        x("c3eaf8437743715d4f"),
    ]


@pytest.mark.parametrize(
    ("capabilities", "algorithm", "reason"),
    [
        ("0100030000000000000000", prov.HMAC_SHA256, None),
        ("0100010000000000000000", prov.CMAC_AES128, None),
        ("0100030002000000000000", None, "oob_required"),
        ("0000010000000000000000", None, "invalid_data"),
    ],
)
def test_algorithm_choice_and_oob_only_devices(capabilities, algorithm, reason):
    p = prov.Provisioner(bytes(range(16)), 0)
    p.invite()
    if reason:
        with pytest.raises(prov.ProvisioningError) as err:
            p.start(x(capabilities))
        assert err.value.reason == reason
    else:
        assert p.start(x(capabilities))[1] == algorithm


def test_failed_pdu_and_invalid_headers():
    with pytest.raises(prov.ProvisioningError) as err:
        prov.parse(x("0902"))
    assert err.value.reason == "invalid_format"
    for data in (b"", x("4100"), x("0100")):
        with pytest.raises(prov.ProvisioningError):
            prov.parse(data)


def test_configuration_messages():
    assert sig.key_indexes(0x456, 0x123) == x("563412")
    assert sig.appkey_add(bytes(range(16))) == x("00000000") + bytes(range(16))
    assert sig.model_app_bind(0x0010, 0x1000) == x("803d100000000010")
    assert sig.model_app_bind(0x0010, 0x0004, 0x07D0) == x("803d10000000d0070400")
    assert sig.model_publication_set(0x0010, 0x0001, 0x1307) == x("03100001000000ff00000713")
    assert sig.composition_get() == x("800800")


# Observed on 2026-10-14 while the Orion was in pairing mode.
ORION_PAIRING = x("dc234f6ea01254156b657935777676720000")


def test_orion_pairing_advertisement():
    device = unprovisioned_device({PROVISIONING: ORION_PAIRING})
    assert device is not None
    assert device.mac == "DC:23:4F:6E:A0:12"
    assert device.category == 0x5415 and device.product_key == b"key5wvvr"
    raw = bytes((21, 0x16, 0x27, 0x18)) + ORION_PAIRING
    assert pairable(SimpleNamespace(raw=raw, service_data={}))
    # HA merges service data: a provisioned lamp keeps an old 0x1827 entry.
    merged = {PROVISIONING: ORION_PAIRING, PROXY: b"\x00" + bytes(8)}
    assert not pairable(SimpleNamespace(raw=None, service_data=merged))
    other = SimpleNamespace(raw=None, service_data={PROVISIONING: ORION_PAIRING[:8] + bytes(10)})
    assert not pairable(other)


def test_unicast_allocation():
    assert allocate_unicast(1, [(1, 1)]) == 0x10
    assert allocate_unicast(2, [(1, 1), (0x10, 1), (0x12, 8)]) == 0x1A
    assert allocate_unicast(1, [(2, 1), (176, 1), (0x10, 1)]) == 0x11
    with pytest.raises(prov.ProvisioningError):
        allocate_unicast(2, [(0x10, 0x7FF0)])
