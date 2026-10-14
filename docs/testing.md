# Verification and remaining hardware work

Test environment: Python 3.14, Home Assistant 2026.10.1 classes, macOS filesystem. Physical HA Green / ESPHome / Orion testing has not occurred.

Offline tests cover documented SIG crypto vectors including Proxy Configuration sample 8.9.1; authenticated network and DeviceKey access packets; Tuya compact DP encoding; unknown and malformed DPs; opt-in HSV conversion; sequence crash/restart/reservation/concurrency; actual disk fsync failure propagation; persistent replay checks; encrypted virtual-peer queries and power writes; segmented color writes with a dropped segment; full report ACKs; replayed reports; HA UI options; dynamically created entities and report-driven state; HA Bluetooth-device selection; MTU-23 GATT SAR, connection failure/reconnect and receive-worker shutdown.

0.2.9b1 adds tests for the passive network check (Network ID, Node Identity hash for the configured address, merged versus raw packet data), the cache-clear rule, link lifetime records that survive packet traffic, and the read-only link probe. The Node Identity hash layout matches two independent implementations (Telink SIG Mesh SDK `proxy_adv_calc_with_node_identity`, Zephyr `node_id_adv`). No official sample vector for it was found. The cached and uncached ESPHome proxy paths cannot be reproduced offline; [link-test-procedure.md](link-test-procedure.md) covers them.

0.3.0b1 adds tests for on-demand connections: the idle disconnect after 60 s, requests and running operations that keep the link open, the refresh that runs only when due and backs off after a failure, the 25 s connect deadline with one attempt, the unchanged persistent mode, and entity availability per mode. These tests use a synthetic peer and short timers. Physical behaviour: [on-demand-test.md](on-demand-test.md).

These tests prove implementation behavior against synthetic peers. They do not prove which protocol variant the FQC8274 firmware implements, that an AppKey is correctly bound on the lamp, physical power/brightness/color, scene completeness or PIR semantics. The README provides the one initial capture needed to resolve the bearer. After that, existing key availability and authenticated responses determine the next controlled test.

Tests use published SIG sample keys and synthetic state only; there are no real device keys in the repository. Third-party HA/backoff deprecation warnings are reported separately from test failures.

0.3.0b1 also adds tests for the schema settings: encoding and rejection outside the schema, entities for every setting, writes through the schema, DP reports published to a group address (read, not acknowledged), replies to another controller (state only, never an answer to our request), and the derived battery warning.

0.3.0b2 removes the tests of the retired entities and adds tests for the motion sensor switch and for the removal of retired entities from the entity registry.

0.4.0b1 adds tests for the network and lamp model: the config flow (a new network with its first lamp, a lamp of a configured network joins it), discovery that offers only identified lamps, the "Add lamp" list and the lamp settings, the migration from version 1, the update listener that skips a detected mesh address, actions that select a lamp device, and device diagnostics. Session tests with two synthetic lamps cover a request through the link of another lamp, detection and storage of an unknown mesh address, the direct connection when a lamp does not answer through the proxy, and the first status read of each lamp through a persistent link. The forwarding is simulated; forwarding between two physical Orion lamps is not tested.

The migration was also run once against the real Home Assistant 2026.10.1 config entry, device and entity registries (a scratch script, not part of the test suite). Observed: when the device moved to the lamp subentry first, HA removed both entities of the old subentry. With the entities moved first, both entities kept their entity IDs and unique IDs. The unit test now checks this order. The full upgrade of a running installation is step 1 of [network-test.md](network-test.md).

0.4.0b2 adds tests for the fixes of D-019: no status refresh before a connectable Bluetooth scanner is registered, a failed connection for a command recorded in the network history, a 5 s timeout for the first request of a link (2 s or more afterwards), and the `uses_device_key` flag that is no longer redacted in diagnostics.
