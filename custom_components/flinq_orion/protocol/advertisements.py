"""Standard mesh service-data classification without guessing Tuya PID offsets."""

PROVISIONING = "00001827-0000-1000-8000-00805f9b34fb"
PROXY = "00001828-0000-1000-8000-00805f9b34fb"


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
