from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.flinq_orion.coordinator import OrionCoordinator


def coordinator(connected):
    return SimpleNamespace(
        session=SimpleNamespace(connected=connected, schedule_connect=Mock()),
        last_monotonic=1,
        last_response_monotonic=1,
        entry=SimpleNamespace(options={"availability_timeout": 300}),
        data={"available": True, "responsive": True},
        async_set_updated_data=Mock(),
    )


def test_idle_connected_lamp_remains_available_without_polling():
    c = coordinator(True)
    with patch("custom_components.flinq_orion.coordinator.time.monotonic", return_value=1000):
        OrionCoordinator.expire(c, None)
    assert c.data == {"available": True, "responsive": True}


def test_disconnected_silent_lamp_expires():
    c = coordinator(False)
    with patch("custom_components.flinq_orion.coordinator.time.monotonic", return_value=1000):
        OrionCoordinator.expire(c, None)
    assert c.data == {"available": False, "responsive": False}


def test_live_connection_does_not_clear_failed_command_state():
    c = coordinator(True)
    c.data["responsive"] = False
    with patch("custom_components.flinq_orion.coordinator.time.monotonic", return_value=1000):
        OrionCoordinator.expire(c, None)
    assert c.data["responsive"] is False
