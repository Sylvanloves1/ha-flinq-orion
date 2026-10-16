"""Provisioner role of the Provisioning protocol with No OOB (Mesh Protocol 1.1, 5.4).

Pure state machine: the caller moves the PDUs over a bearer. Values are big-endian
(Mesh 5.1). The HMAC algorithm is used when the device offers it (Mesh 5.4.2.4).
"""

from dataclasses import dataclass
from hmac import compare_digest
import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from .crypto import cmac, hmac_sha256, k1, k5, s1, s2
from .errors import MeshError

INVITE, CAPABILITIES, START, PUBLIC_KEY, INPUT_COMPLETE = range(5)
CONFIRMATION, RANDOM, DATA, COMPLETE, FAILED = range(5, 10)
CMAC_AES128, HMAC_SHA256 = 0, 1
# Capabilities OOB Type bit 1: the device allows only OOB authentication.
ONLY_OOB = 0x02
PARAMETER_LENGTHS = {
    CAPABILITIES: (11,),
    PUBLIC_KEY: (64,),
    INPUT_COMPLETE: (0,),
    CONFIRMATION: (16, 32),
    RANDOM: (16, 32),
    COMPLETE: (0,),
    FAILED: (1,),
}
FAILED_REASONS = {
    0x01: "invalid_pdu",
    0x02: "invalid_format",
    0x03: "unexpected_pdu",
    0x04: "confirmation_failed",
    0x05: "out_of_resources",
    0x06: "decryption_failed",
    0x07: "unexpected_error",
    0x08: "cannot_assign_addresses",
    0x09: "invalid_data",
}


class ProvisioningError(MeshError):
    """Pairing stopped; `reason` is a stable identifier for the UI."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class Capabilities:
    elements: int
    algorithms: int
    public_key_type: int
    oob_type: int
    output_oob_size: int
    output_oob_action: int
    input_oob_size: int
    input_oob_action: int

    @classmethod
    def decode(cls, data: bytes) -> "Capabilities":
        return cls(
            data[0],
            int.from_bytes(data[1:3], "big"),
            data[3],
            data[4],
            data[5],
            int.from_bytes(data[6:8], "big"),
            data[8],
            int.from_bytes(data[9:11], "big"),
        )


def pdu(kind: int, parameters: bytes = b"") -> bytes:
    return bytes((kind,)) + parameters


def parse(data: bytes) -> tuple[int, bytes]:
    """Type and parameters of a received Provisioning PDU; Failed raises."""
    if not data or data[0] & 0xC0:
        raise ProvisioningError("invalid_pdu", "Invalid Provisioning PDU header")
    kind, parameters = data[0], data[1:]
    if kind not in PARAMETER_LENGTHS or len(parameters) not in PARAMETER_LENGTHS[kind]:
        raise ProvisioningError("invalid_pdu", f"Unexpected Provisioning PDU type {kind}")
    if kind == FAILED:
        reason = FAILED_REASONS.get(parameters[0], "unexpected_error")
        raise ProvisioningError(reason, f"The lamp stopped pairing ({reason})")
    return kind, parameters


def public_key_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    numbers = key.public_numbers()
    return numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


class Provisioner:
    """One provisioning run. Call the methods in protocol order."""

    def __init__(
        self,
        net_key: bytes,
        iv_index: int,
        *,
        key_index: int = 0,
        attention: int = 5,
        private_key: ec.EllipticCurvePrivateKey | None = None,
        random=os.urandom,
    ):
        if len(net_key) != 16 or not 0 <= iv_index <= 0xFFFFFFFF or not 0 <= key_index <= 0xFFF:
            raise ValueError("Invalid network parameters")
        self.net_key, self.iv_index, self.key_index = net_key, iv_index, key_index
        self.attention = attention
        self.private_key = private_key or ec.generate_private_key(ec.SECP256R1())
        self.own_public = public_key_bytes(self.private_key.public_key())
        self.random_source = random
        self.capabilities: Capabilities | None = None
        self.inputs = b""
        self.algorithm = CMAC_AES128
        self.ecdh = self.salt = self.confirmation_key = self.random = b""
        self.device_confirmation = self.own_confirmation = self.device_random = b""
        self.device_key: bytes | None = None

    def invite(self) -> bytes:
        value = bytes((self.attention,))
        self.inputs = value
        return pdu(INVITE, value)

    def start(self, capabilities: bytes) -> bytes:
        caps = Capabilities.decode(capabilities)
        if caps.elements == 0:
            raise ProvisioningError("invalid_data", "The lamp reports no elements")
        if caps.oob_type & ONLY_OOB:
            raise ProvisioningError("oob_required", "The lamp only allows OOB authentication")
        if caps.algorithms & 0x02:
            self.algorithm = HMAC_SHA256
        elif caps.algorithms & 0x01:
            self.algorithm = CMAC_AES128
        else:
            raise ProvisioningError("invalid_data", "The lamp offers no known algorithm")
        self.capabilities = caps
        # No OOB public key, No OOB authentication: action and size are 0 (Mesh 5.4.1.3).
        value = bytes((self.algorithm, 0, 0, 0, 0))
        self.inputs += capabilities + value
        return pdu(START, value)

    def public_key(self) -> bytes:
        return pdu(PUBLIC_KEY, self.own_public)

    def confirmation(self, device_public: bytes) -> bytes:
        if compare_digest(device_public, self.own_public):
            raise ProvisioningError("invalid_data", "The lamp returned our public key")
        try:
            device_key = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), b"\x04" + device_public
            )
        except ValueError as err:
            raise ProvisioningError("invalid_data", "Invalid public key of the lamp") from err
        self.ecdh = self.private_key.exchange(ec.ECDH(), device_key)
        inputs = self.inputs + self.own_public + device_public
        if self.algorithm == HMAC_SHA256:
            self.salt = s2(inputs)
            self.confirmation_key = k5(self.ecdh + bytes(32), self.salt, b"prck256")
            self.random = self.random_source(32)
        else:
            self.salt = s1(inputs)
            self.confirmation_key = k1(self.ecdh, self.salt, b"prck")
            self.random = self.random_source(16)
        self.own_confirmation = self._confirm(self.random)
        return pdu(CONFIRMATION, self.own_confirmation)

    def _confirm(self, random: bytes) -> bytes:
        if self.algorithm == HMAC_SHA256:
            return hmac_sha256(self.confirmation_key, random)
        return cmac(self.confirmation_key, random + bytes(16))

    def random_pdu(self, device_confirmation: bytes) -> bytes:
        if len(device_confirmation) != len(self.own_confirmation):
            raise ProvisioningError("invalid_pdu", "Confirmation length does not match")
        if compare_digest(device_confirmation, self.own_confirmation):
            raise ProvisioningError("confirmation_failed", "The lamp echoed our confirmation")
        self.device_confirmation = device_confirmation
        return pdu(RANDOM, self.random)

    def verify(self, device_random: bytes):
        """Check the confirmation of the lamp; before an address is given out."""
        if len(device_random) != len(self.random) or compare_digest(device_random, self.random):
            raise ProvisioningError("confirmation_failed", "Invalid random of the lamp")
        if not compare_digest(self._confirm(device_random), self.device_confirmation):
            raise ProvisioningError("confirmation_failed", "Confirmation of the lamp failed")
        self.device_random = device_random

    def data(self, unicast: int) -> bytes:
        if not self.device_random:
            raise ProvisioningError("unexpected_pdu", "The lamp was not verified")
        if not 1 <= unicast <= 0x7FFF:
            raise ValueError("Invalid unicast address")
        salt = s1(self.salt + self.random + self.device_random)
        session_key = k1(self.ecdh, salt, b"prsk")
        nonce = k1(self.ecdh, salt, b"prsn")[3:]
        self.device_key = k1(self.ecdh, salt, b"prdk")
        data = (
            self.net_key
            + self.key_index.to_bytes(2, "big")
            + bytes(1)  # flags: no key refresh, no IV update
            + self.iv_index.to_bytes(4, "big")
            + unicast.to_bytes(2, "big")
        )
        return pdu(DATA, AESCCM(session_key, tag_length=8).encrypt(nonce, data, None))
