# FlinQ Orion FQC8274 — experimental local integration

Version **0.2.0** is a new independent Home Assistant custom integration, not a fork or wrapper. It includes an active local Tuya SIG Mesh client through Home Assistant's Bluetooth stack and ESPHome Bluetooth Proxies. **The code is tested offline; physical Orion firmware compatibility remains unverified.** Selecting a protocol enables an experiment, not a claim that all FQC8274 features work.

Implemented:

- UI setup/options, discovery for the supplied Orion MAC, GATT/service-data diagnostics.
- Existing-network onboarding using NetKey/AppKey, optional DeviceKey, IV Index and a dedicated unused controller unicast address. No cloud calls, automatic provisioning or factory reset.
- Authenticated proxy filter configuration, AES-CMAC/CCM network/access traffic, DeviceKey messages, segmented access transmission/reception, complete ACKs and missing-segment retransmission.
- Serialized requests, source/opcode/value response matching, read-only query retries, report-driven state, persistent replay protection, automatic GATT reconnect with backoff.
- Crash-safe sequence ranges saved through atomic replace and file/directory fsync, outside the HA event loop. Write failures stop transmission. Sequence storage is scoped to the network/controller address, not an entity or config-entry ID.
- Conditional light with power/brightness, optional Kelvin/HS (HA also translates RGB actions), captured scenes, observed mode select and a countdown number with confirmed units/range.
- Unknown DPs remain diagnostics. No DP51–59 meanings are guessed or writable.
- HA diagnostics, EN/NL UI, HACS structure, CI and protocol/entity/session/storage tests.

Missing/unverified: automatic acquisition of Smart Life mesh keys, new-device provisioning/Tuya authentication, exact Orion wire format/mode codes, CT endpoints/scaling, all scene enumeration, PIR mappings, timestamp response format, automated IV Update/recovery and key refresh. Secure beacons are authenticated; incompatible IV/key-refresh transitions block further transmission rather than guess a new state. Segmentation supports direct unicast peers, not Friend/OBO acknowledgements or virtual/group control.

## Installation

Copy `custom_components/flinq_orion` to `/config/custom_components/flinq_orion` and restart HA. Alternatively, after this repository is published, add `https://github.com/Sylvanloves1/ha-flinq-orion` in HACS → Custom repositories → Integration.

Add **FlinQ Orion** via Settings → Devices & services or discovery. Without mesh keys, observation mode remains usable. The RSSI entity is created immediately. Other entities appear after authenticated reports establish the relevant fields and you select the experimental protocol. Device firmware 5.1 is the supplied profile metadata, not a firmware version independently read by this integration.

## One first hardware test (no keys required)

1. Install version **0.2.0**, restart HA, and add FlinQ Orion with `DC:23:4F:6E:A0:12`. Leave Options at `observe`.
2. Keep the lamp powered, close Smart Life completely and ensure `bluetooth-proxy-buiten` has a free active connection slot.
3. In Developer tools → Actions run:

   ```yaml
   action: logger.set_level
   data:
     custom_components.flinq_orion: debug
   ```

4. Run **FlinQ Orion: Capture Bluetooth diagnostics** (`flinq_orion.capture`), select its config entry, duration **20 seconds**. Do not operate Smart Life during capture.
5. Download diagnostics from the integration entry. Return only `state.gatt` and `packets`, or the matching `ORION RX` log lines. Inspect raw advertisement identifiers before sharing. Never send keys, a full backup or an unsanitized HCI snoop capture.

This establishes whether the lamp exposes Mesh Proxy `0x1828`, Provisioning `0x1827`, or another Tuya bearer. Strong RSSI is not proof of a usable command bearer. Ordinary ESPHome Bluetooth Proxies do not replace a SIG Mesh Proxy Server. HA advertisement callbacks do not expose arbitrary raw AD type `0x2a` mesh bearer packets, so Smart Life traffic is not guaranteed to appear in this capture.

## Activate the experimental command path when existing keys are available

In Options select:

| Protocol | Behavior |
|---|---|
| `observe` | Passive discovery/capture; no mesh transmission |
| `query_only` | Authenticated proxy setup, read-only DP/composition discovery; no light control |
| `vendor_dp` | Experimental documented Tuya compact DP reads/writes; fields must first be observed |
| `sig` | Standard SIG light queries/sets; requires observed model status; earlier generic OnOff failure remains relevant |

Enter the existing NetKey/AppKey and current IV Index. DeviceKey is optional for composition and the explicit vendor-bind action. Keys cannot be derived from normal encrypted advertisements and are never fetched from cloud by this version.

Enter a **dedicated never-used unicast controller address**, in decimal, distinct from all lamp elements and every Smart Life/gateway/controller/node address. Do not copy Smart Life's source address or pick a random address without checking the existing mesh allocation. Check the unique-source confirmation only after verification. Node address defaults to **176 (`0x00B0`)**, the supplied primary lamp address; element routing uses composition when available.

Leave unconfirmed profile switches off. CT requires proven Kelvin endpoints **and a linear Kelvin mapping**; endpoints alone do not establish scaling. HSV requires confirmed 12-character encoding. Optional mode values must have an observed enum report and confirmed meaning; `-1` leaves mode selection manual. Scenes can only replay captured DP6 blobs after explicit enablement; names `Scene 1`, etc. are capture labels, not an exhaustive firmware scene list. Countdown requires confirmed seconds and maximum.

After reload, the integration connects and performs bounded read-only discovery. It never automatically adds keys or binds models. If the vendor model requires a binding to an already installed AppKey, the advanced `bind_vendor_model` action can add that binding using the existing DeviceKey, known AppKey index and explicit `confirm: true`. This is a configuration write; do not use it as a speculative response to every timeout.

Turn the discovered light on/off and inspect both the physical lamp and the reports. No optimistic state is used. A matching report confirms protocol state, not independently observed physical light output. If no reports arrive, inspect diagnostics for NID/MIC/IV errors, proxy filter timeouts and GATT services instead of guessing values.

## Security and maintenance

HA config-entry files store keys locally without additional encryption. Password selectors mask the UI; diagnostics redact keys. Protect HA files/backups. Durable sequence/replay files contain counters and a network fingerprint, not secret keys. Do not edit/delete them. Restoring an old backup or losing cryptographic state requires a new verified unused controller address or external validated recovery; a restored counter cannot establish that intervening sequence numbers were never transmitted.

No daily Internet or Smart Life dependency exists in the command path. HA restart reloads credentials and skips unused sequence reservations. There is no reset or arbitrary packet-injection action. See [protocol findings](docs/protocol.md), [development](docs/development.md) and [test status](docs/testing.md).
