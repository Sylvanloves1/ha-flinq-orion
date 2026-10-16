"""SIG model opcode constants verified against Espressif ESP-IDF definitions."""

ONOFF_GET, ONOFF_SET, ONOFF_STATUS = 0x8201, 0x8202, 0x8204
LIGHTNESS_GET, LIGHTNESS_SET, LIGHTNESS_STATUS = 0x824B, 0x824C, 0x824E
TEMP_GET, TEMP_RANGE_GET, TEMP_RANGE_STATUS = 0x8261, 0x8262, 0x8263
TEMP_SET, TEMP_STATUS = 0x8264, 0x8266
HSL_GET, HSL_SET, HSL_STATUS = 0x826D, 0x8276, 0x8278
COMPOSITION_GET, COMPOSITION_STATUS = 0x8008, 0x02
MODEL_APP_BIND, MODEL_APP_STATUS = 0x803D, 0x803E

# Configuration messages, sent with the DeviceKey (Mesh Protocol 1.1, 4.3.2). Access
# parameters are little-endian (Mesh 4.1.1).
APPKEY_ADD, APPKEY_STATUS = 0x00, 0x8003
MODEL_PUBLICATION_SET, MODEL_PUBLICATION_STATUS = 0x03, 0x8019
STATUS_SUCCESS = 0x00
# Configuration and Health models take no AppKey binding.
FOUNDATION_MODELS = frozenset({0x0000, 0x0001, 0x0002, 0x0003})
# Publish TTL 0xFF: use the default TTL of the node.
DEFAULT_TTL = 0xFF


def key_indexes(net_index: int, app_index: int) -> bytes:
    """Two 12-bit key indexes packed into 3 octets (Mesh 4.3.1.1)."""
    return ((app_index << 12) | net_index).to_bytes(3, "little")


def model_id(model: int, company: int | None = None) -> bytes:
    if company is None:
        return model.to_bytes(2, "little")
    return company.to_bytes(2, "little") + model.to_bytes(2, "little")


def appkey_add(app_key: bytes, net_index: int = 0, app_index: int = 0) -> bytes:
    return bytes((APPKEY_ADD,)) + key_indexes(net_index, app_index) + app_key


def composition_get(page: int = 0) -> bytes:
    return COMPOSITION_GET.to_bytes(2, "big") + bytes((page,))


def model_app_bind(
    element: int, model: int, company: int | None = None, app_index: int = 0
) -> bytes:
    return (
        MODEL_APP_BIND.to_bytes(2, "big")
        + element.to_bytes(2, "little")
        + app_index.to_bytes(2, "little")
        + model_id(model, company)
    )


def model_publication_set(
    element: int,
    publish_address: int,
    model: int,
    company: int | None = None,
    app_index: int = 0,
    ttl: int = DEFAULT_TTL,
    period: int = 0,
    retransmit: int = 0,
) -> bytes:
    """Publication with the AppKey (credential flag 0); period 0 publishes on change."""
    return (
        bytes((MODEL_PUBLICATION_SET,))
        + element.to_bytes(2, "little")
        + publish_address.to_bytes(2, "little")
        + (app_index & 0xFFF).to_bytes(2, "little")
        + bytes((ttl, period, retransmit))
        + model_id(model, company)
    )
