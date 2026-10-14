"""Read documented config-entry fields without importing another integration.

This is optional local onboarding only. No runtime calls, controller state,
sequence numbers, cloud credentials or legacy mesh passwords are imported.
"""

import re
from hmac import compare_digest
from .protocol.advertisements import identity_material, node_identity_hash
from .protocol.beacon import decode_secure_beacon
from .protocol.errors import MeshError
from .const import PROXY_SERVICE

PROVIDER_DOMAIN = "tuya_ble_mesh"


class CredentialImportError(MeshError):
    """Only stable translation identifiers are exposed by the importer."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def normalize_address(value):
    return str(value).replace(":", "").replace("-", "").upper()


def matching_entries(entries, address):
    return [
        entry
        for entry in entries
        if entry.domain == PROVIDER_DOMAIN
        and normalize_address({**entry.data, **entry.options}.get("mac_address", ""))
        == normalize_address(address)
    ]


def _key(value, optional=False):
    if optional and not value:
        return ""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9a-fA-F]{32}", value.strip())
        or value.strip() == "0" * 32
    ):
        raise CredentialImportError("import_missing_keys")
    return value.strip().lower()


def _address(value):
    # tuya_ble_mesh documents its string unicast fields as hexadecimal.
    try:
        address = int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise CredentialImportError("import_invalid_metadata") from None
    if not 1 <= address <= 0x7FFF:
        raise CredentialImportError("import_invalid_metadata")
    return address


def import_credentials(entry, address):
    if not matching_entries([entry], address):
        raise CredentialImportError("import_wrong_device")
    data = {**entry.data, **entry.options}
    net = _key(data.get("net_key"))
    app = _key(data.get("app_key"))
    device = _key(data.get("dev_key"), optional=True)
    node = _address(data.get("unicast_target"))
    try:
        iv = int(data["iv_index"])
    except (KeyError, TypeError, ValueError):
        raise CredentialImportError("import_invalid_metadata") from None
    if not 0 <= iv <= 0xFFFFFFFF:
        raise CredentialImportError("import_invalid_metadata")
    old_source = _address(data["unicast_our"]) if data.get("unicast_our") is not None else None
    return {
        "net_key": net,
        "app_key": app,
        "device_key": device,
        "node_address": node,
        "iv_index": iv,
    }, old_source


def verify_network_evidence(credentials, service_data, records=()):
    """Reject mismatch when a known standard identity/beacon is available.

    AppKey/DeviceKey cannot be independently validated from these advertisements.
    An absence of verifiable evidence is reported, never converted into success.
    """
    key = bytes.fromhex(credentials["net_key"])
    network_id, identity_key = identity_material(key)
    value = service_data.get(PROXY_SERVICE)
    verified = False
    if value and len(value) == 9 and value[0] == 0:
        if not compare_digest(value[1:], network_id):
            raise CredentialImportError("import_network_mismatch")
        verified = True
    elif value and len(value) == 17 and value[0] == 1:
        hashed = node_identity_hash(identity_key, value[9:], credentials["node_address"])
        if not compare_digest(hashed, value[1:9]):
            raise CredentialImportError("import_network_mismatch")
        verified = True
    for record in records:
        if record.get("kind") == "proxy" and record.get("type") == 1:
            try:
                payload = bytes.fromhex(record.get("payload", ""))
            except ValueError:
                continue
            if len(payload) == 22 and payload[0] == 1:
                try:
                    beacon = decode_secure_beacon(payload, key)
                except MeshError:
                    raise CredentialImportError("import_network_mismatch") from None
                if beacon.iv_index != credentials["iv_index"]:
                    raise CredentialImportError("import_iv_mismatch")
                if beacon.key_refresh:
                    raise CredentialImportError("import_key_refresh")
                verified = True
    return verified
