# Verification and remaining hardware work

Test environment: Python 3.14, Home Assistant 2026.10.1 classes, macOS filesystem. Physical HA Green / ESPHome / Orion testing has not occurred.

Offline tests cover documented SIG crypto vectors including Proxy Configuration sample 8.9.1; authenticated network and DeviceKey access packets; Tuya compact DP encoding; unknown and malformed DPs; opt-in HSV conversion; sequence crash/restart/reservation/concurrency; actual disk fsync failure propagation; persistent replay checks; encrypted virtual-peer queries and power writes; segmented color writes with a dropped segment; full report ACKs; replayed reports; HA UI options; dynamically created entities and report-driven state; HA Bluetooth-device selection; MTU-23 GATT SAR, connection failure/reconnect and receive-worker shutdown.

These tests prove implementation behavior against synthetic peers. They do not prove which protocol variant the FQC8274 firmware implements, that an AppKey is correctly bound on the lamp, physical power/brightness/color, scene completeness or PIR semantics. The README provides the one initial capture needed to resolve the bearer. After that, existing key availability and authenticated responses determine the next controlled test.

Tests use published SIG sample keys and synthetic state only; there are no real device keys in the repository. Third-party HA/backoff deprecation warnings are reported separately from test failures.
