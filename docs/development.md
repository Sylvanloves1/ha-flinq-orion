# Development

Use Python 3.14 and a virtualenv; install with `pip install -r requirements-dev.txt`.

Run `python -m pytest`, `ruff check custom_components tests`, `ruff format --check custom_components tests`, and `mypy custom_components/flinq_orion`.

The protocol package has no Home Assistant dependency. HA-specific filesystem/connection code sits in `storage.py` and `session.py`; device reports/capability rules in `device.py` and `device_profiles`; entity platforms consume those rules. Read-only discovery is independent of power/control writes. Entities are added once when report-based capabilities become available and update with subsequent reports.

The active controller implements the documented managed-flooding GATT path, not every mandatory feature of a qualified SIG Mesh node. Direct unicast segment ACKs are supported; group/virtual/Friend/OBO control, automatic provisioning, key refresh and automated IV transitions are not. Unknown Tuya timestamp/mode/scene formats must be documented from evidence before adding another encoding path.

Hardware validation is still required. Change exactly one Smart Life parameter per future experiment and record timestamps, previous/new values, reports and captures. Do not assume Smart Life and HA can maintain simultaneous GATT connections. Android Bluetooth HCI snoop may provide app-side ATT writes when HA cannot receive them, but such captures can contain mesh keys; sanitize locally before sharing.

HA Store suppresses some write errors. It remains suitable for observation snapshots, while counters/RPL use the dedicated DurableStore with explicit propagated errors. Never substitute delayed observation persistence for write-ahead sequence reservations. Never reset sequence to recover an IV mismatch.
