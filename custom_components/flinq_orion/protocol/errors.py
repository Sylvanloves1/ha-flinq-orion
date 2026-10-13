class FlinqOrionError(Exception):
    """Base protocol error."""


class MeshError(FlinqOrionError):
    """Malformed or unsupported mesh packet."""


class MeshAuthenticationError(MeshError):
    """Credentials or authentication failed."""


class MeshDecryptionError(MeshAuthenticationError):
    """MIC validation failed."""


class MeshTimeoutError(MeshError):
    """Timed out waiting for a response."""


class TuyaDPError(FlinqOrionError):
    """Invalid DP encoding."""


class DeviceUnavailableError(FlinqOrionError):
    """No reachable Bluetooth endpoint."""
