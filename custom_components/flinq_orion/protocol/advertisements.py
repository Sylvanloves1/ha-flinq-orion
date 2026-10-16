"""Standard mesh service-data classification without guessing Tuya PID offsets."""

from dataclasses import dataclass
from hmac import compare_digest
from .crypto import aes_block, k1, k3, s1

PROVISIONING = "00001827-0000-1000-8000-00805f9b34fb"
PROXY = "00001828-0000-1000-8000-00805f9b34fb"
SERVICE_DATA_16 = 0x16


def classify(service_data: dict[str, bytes]) -> list[dict]:
    result = []
    for uuid, data in service_data.items():
        uuid = uuid.lower()
        if uuid == PROVISIONING:
            item = {"kind": "provisioning_service_data", "length": len(data)}
            if len(data) == 18:
                item.update(
                    format="standard_unprovisioned_device",
                    device_uuid=data[:16].hex(),
                    oob_information_hex=data[16:18].hex(),
                )
            else:
                item["format"] = "unknown"
            result.append(item)
        elif uuid == PROXY:
            item = {
                "kind": "proxy_service_data",
                "length": len(data),
                "identity_type": data[0] if data else None,
            }
            if len(data) == 9 and data[0] == 0:
                item.update(format="network_identity", network_id=data[1:].hex())
            elif len(data) == 17 and data[0] == 1:
                item.update(format="node_identity", hash=data[1:9].hex(), random=data[9:].hex())
            else:
                item["format"] = "unknown"
            result.append(item)
    return result


def raw_service_data(raw: bytes) -> dict[str, bytes]:
    """16-bit UUID service data of one advertisement packet; a malformed tail is ignored.

    HA merges service data across packets of a device. The raw packet shows what the
    lamp sent last, so one old provisioning packet is not reported as current state.
    """
    result: dict[str, bytes] = {}
    offset = 0
    while offset < len(raw):
        length = raw[offset]
        if length == 0 or offset + 1 + length > len(raw):
            break
        value = raw[offset + 2 : offset + 1 + length]
        if raw[offset + 1] == SERVICE_DATA_16 and len(value) >= 2:
            uuid = int.from_bytes(value[:2], "little")
            result[f"0000{uuid:04x}-0000-1000-8000-00805f9b34fb"] = value[2:]
        offset += 1 + length
    return result


@dataclass(frozen=True)
class UnprovisionedDevice:
    """Tuya device UUID: MAC (6) + mesh category (2) + product key (8 ASCII)."""

    mac: str
    category: int
    product_key: bytes


def unprovisioned_device(service_data: dict[str, bytes]) -> UnprovisionedDevice | None:
    """Unprovisioned Device beacon data of the 0x1827 service (Mesh 7.1.2.2.1)."""
    data = service_data.get(PROVISIONING)
    if data is None or len(data) != 18:
        return None
    return UnprovisionedDevice(
        ":".join(f"{byte:02X}" for byte in data[:6]),
        int.from_bytes(data[6:8], "big"),
        bytes(data[8:16]),
    )


def identity_material(net_key: bytes) -> tuple[bytes, bytes]:
    """Network ID and IdentityKey derived from the NetKey (Mesh Profile 3.8.6.3)."""
    return k3(net_key), k1(net_key, s1(b"nkik"), b"id128\x01")


def node_identity_hash(identity_key: bytes, random: bytes, node_address: int) -> bytes:
    """Node Identity hash (Mesh Profile 7.2.2.2.4): 48 zero bits, random and address."""
    return aes_block(identity_key, bytes(6) + random + node_address.to_bytes(2, "big"))[8:]


def network_evidence(
    proxy_data: bytes | None, network_id: bytes, identity_key: bytes, node_address: int
) -> str:
    """Check proxy advertising against the configured network and node without connecting."""
    if not proxy_data:
        return "no_proxy_identity"
    if len(proxy_data) == 9 and proxy_data[0] == 0:
        return "network_id_match" if compare_digest(proxy_data[1:], network_id) else "mismatch"
    if len(proxy_data) == 17 and proxy_data[0] == 1:
        expected = node_identity_hash(identity_key, proxy_data[9:], node_address)
        return "node_identity_match" if compare_digest(proxy_data[1:9], expected) else "mismatch"
    if proxy_data[0] in (2, 3):
        return "private_identity_unverified"
    return "unknown_proxy_identity"
