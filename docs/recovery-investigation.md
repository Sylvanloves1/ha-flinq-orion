# Recovery audit and Smart Life comparison

## Evidence

The version 0.2.1 capture (file 2) contains 20 authenticated access responses
between 09:04:09.591605 and 09:04:53.018190 UTC. User confirmed physical power
control; diagnostics at capture time were connected/responsive with no failed
commands. This is a reference that worked for at least 43 seconds, not proof of
long-term stability or all features.

Commits 0.2.2–0.2.7 did not change the sequence allocator, durable storage, config
flow or credential import. Keys/controller address/sequence storage must remain
in place during comparison. Version 0.2.2 shortened discovery response waits,
added explicit zero transition/delay and skipped redundant power-on commands.
The first unavailable report was already on 0.2.2, whose connection setup still
matched 0.2.1. Therefore forced uncached service discovery in 0.2.7 is not a
sufficient explanation of the original regression. It is nevertheless a later
connection-policy difference that must be removed from a baseline comparison.

Version 0.2.7 forces uncached discovery. Proxy captures show MTU 250 and parameter
updates. The earlier proxy capture showed a cached connection with MTU 23. This
correlation does not establish MTU as the cause. The beta timeout request failed
in GAP status events; it must not become the default.

## Exact reference package

`artifacts/flinq_orion-0.2.1-reference.zip` is made using git archive from commit
50d510f, not a new approximation or fork. All 66 original tests pass against this
snapshot with the current development dependencies. It intentionally retains
the original slow discovery behavior, so responsiveness is not the outcome
measure; first establish whether the original connection remains operational.
Never delete entries or sequence files, reuse source 1, reset or re-provision
the lamp as part of this comparison.

## What public Smart Life SDK material establishes

Tuya's Android/iOS SIG Mesh APIs start a local client/proxy search, connect to
provisioned mesh devices and publish category-dependent DP commands. The API
documentation does not disclose the actual GATT ordering, MTU negotiation,
connection interval choices or vendor handshake of the user's Smart Life build.
An unrelated legacy Tuya mesh or Tuya single-point BLE SDK must not be used as
proof of this device's behavior. Standard SIG replies already establish that
our keys and network encoding can work for this node.

Sources:
- https://developer.tuya.com/en/docs/app-development/meshsig?id=Ka6km4aeuygxr
- https://developer.tuya.com/en/docs/app-development/sigmesh?id=Kceufh7df3uhq
- https://source.android.com/docs/core/connect/bluetooth/verifying_debugging

Android's HCI snoop captures the app's controller traffic without decrypting an
APK or modifying Smart Life. It can establish GATT handles, descriptors, MTU
requests, connection updates and access-write timing. Mesh access PDUs remain
mesh-encrypted and require existing keys for protocol interpretation. A full
bugreport can contain unrelated personal information; request only the extracted
Bluetooth trace for this controlled experiment. A phone platform answer is
needed before providing the exact collection workflow. Smart Life was removed
from the device; do not assume it can be re-added without new provisioning/keys.

## iPhone route

User confirmed iPhone. Apple provides PacketLogger in Additional Tools for Xcode
and an iOS Bluetooth diagnostic logging profile. Apple's WWDC19 demonstration
uses a wired iOS device, the logging profile and File > New iOS Trace on a Mac.
Current OS/profile compatibility must be checked against Apple's download
instructions. Do not propose Android HCI snoop or claim access to Smart Life
source. Smart Life currently has no configured lamp, so capturing an actively
working Smart Life session would require resolving onboarding first; avoid
re-provisioning the existing node merely to collect a trace.

Official sources:
- https://developer.apple.com/bluetooth/
- https://developer.apple.com/feedback-assistant/profiles-and-logs/
- https://developer.apple.com/videos/play/wwdc2019/901/

Reference snapshot published explicitly as a prerelease:
https://github.com/Sylvanloves1/ha-flinq-orion/releases/tag/v0.2.1
No changes to main or persisted HA sequence state were made.

## Corrections after the source audit (0.2.9b1)

- MTU: the statement "the earlier proxy capture showed a cached connection with MTU 23"
  is a reporting artefact. ESPHome 2026.9.1 sends the MTU request for both connection
  types and reports 23 for every cached connection. The MTU correlation is withdrawn.
- status=16 is now traced: `ESP_BT_STATUS_TIMEOUT` from Bluedroid's host-side
  connection-update timer (1–2 s). It is not a rejection by the lamp.
- The 40 ms / 2 s update matches the connect-time request of Telink SIG Mesh SDK builds
  from 2020–2022. It is very probably requested by the lamp.
- The 0.2.1 sessions were probably cached connections. This is inferred from the code and
  the running Home Assistant session, not observed in a proxy log.
- The lamp changed its advertising from Network ID (working sessions) to Node Identity
  (all failing sessions), and showed a provisioning GATT table once at 12:15 UTC. The lamp
  state is a confounding factor for every comparison after 09:04 UTC.
- Diagnostics `data.version` was hard-coded. Captures 3–5 ran 0.2.2 and 0.2.3, not 0.2.1.

Details and the link table: [protocol.md](protocol.md), section "Source audit and capture
correlation (0.2.9b1)". Test: [link-test-procedure.md](link-test-procedure.md).
