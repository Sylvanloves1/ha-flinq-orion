import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def bluetooth_scanner():
    """One connectable Bluetooth scanner is registered, unless a test says otherwise."""
    with patch(
        "custom_components.flinq_orion.session.bluetooth.async_scanner_count", return_value=1
    ) as count:
        yield count
