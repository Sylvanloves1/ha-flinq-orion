# FlinQ Orion FQC8274: experimental local integration

Version **0.4.0b2** is a new independent Home Assistant custom integration, not a fork or wrapper. It includes an active local Tuya SIG Mesh client through Home Assistant's Bluetooth stack and ESPHome Bluetooth Proxies. **Local SIG power control has been confirmed on the Orion; other features still require physical verification.** Selecting a protocol enables an experiment, not a claim that all FQC8274 features work.

## The Orion runs on a battery

The Orion is a solar lamp with a Li-ion battery. With a low battery it wakes for a few seconds about every 3 minutes and no Bluetooth link works. Charge it over USB-C when it gets little sun. Do not use pairing mode or a power-cycle sequence as a fix: three quick power toggles reset Tuya lamps. Background: [docs/protocol.md](docs/protocol.md), sections from "Passive monitor 2" on.

## Networks and lamps (0.4.0b1)

0.4.0b1 is a test prerelease. A FlinQ Orion entry is now one **mesh network**: the keys, the controller address and the connection mode are set once in its Options. Each lamp is a **lamp subentry** of that network.

| Task | How |
|---|---|
| Upgrade from 0.3.x | Automatic. The existing entry becomes the network and the lamp becomes its first lamp subentry. Keys, controller address 2, the sequence and replay state, the entity IDs and their history stay the same. |
| Add a lamp | Open the network entry and select **Add lamp**. The list shows nearby mesh proxies, with lamps that advertise the Network ID of these keys first ("this network"). A lamp with a low battery advertises only Node Identity and shows as "network not verified". You can also type the Bluetooth address. |
| Discovery | Home Assistant offers a lamp found over Bluetooth only when it advertises the Network ID of a configured network, or when no network is configured and a Tuya BLE Mesh entry holds that lamp. Other mesh devices are ignored. |
| Mesh address | Detected on the first direct connection, from the source of the lamp's Proxy Filter Status, and stored with the lamp. Change it, the name or the optional DeviceKey with **Reconfigure** on the lamp (0 = detect again). |
| Actions | The `flinq_orion.*` actions select a lamp **device**, not a config entry. Scripts that use `entry_id` must change to `device_id`. |

One Bluetooth link serves the whole network (D-015). A command for another lamp goes through the lamp that holds the link. If that lamp does not answer, the integration connects to it directly and sends the command once more. Forwarding between these lamps is not proven yet: test it with two lamps and report the diagnostics. Each lamp has its own device diagnostics; the network entry diagnostics show the link history and all lamps.

## Connection mode (0.3.0b1)

0.3.0b1 is a test prerelease. The option **Connection mode** controls the Bluetooth link:

| Mode | Behaviour |
|---|---|
| `on_demand` (default) | A command opens a link; the link closes after 60 s without activity. The status is refreshed when Home Assistant starts and then every 30 minutes, when the lamp is seen. If the lamp cannot be reached within about 20 s, the command fails with "Lamp not reachable over Bluetooth". The light is available when the lamp advertised within the availability timeout. |
| `persistent` | The 0.2.9b1 behaviour: keep one link open and reconnect with backoff. The light is available only while the link is up. |

The first command after an idle period takes longer (measured: 1–4 s to connect and set up). Follow-up commands within 60 s use the open link. Since 0.4.0b1 the connection mode applies to the whole network. The lamp does not report its own changes, such as PIR switching, to Home Assistant in either mode. Decisions: [docs/decision-log.md](docs/decision-log.md), D-009 and D-010.

## Functions (0.3.0b2)

| Entity | Source | Notes |
|---|---|---|
| Light: on/off, brightness, colour | DP 1, 3, 5 via SIG models | Only on/off is confirmed physically |
| Light: colour temperature | DP 4 | 2000–6500 K assumed, as Home Assistant's Tuya integration used |
| Motion sensor (on/off) | DP 56 | |
| Battery status (Normal, Empty) | derived | "Empty" when the lamp shows its low-power advertising cycle; the lamp has no battery data point |
| Signal strength | advertisements | |

0.3.0b2 removes the light mode select, the PIR state sensor and all configuration entities of 0.3.0b1 (D-013). They are also removed from the entity registry. The data point schema stays in the code (`device_profiles`), so the settings can come back without new research.

0.2.9b1 changes that remain: the GATT service cache policy of 0.2.1, the passive check that the lamp still advertises the configured network and unicast address, the connection history in diagnostics and the read-only `flinq_orion.probe_link` action.

Implemented:

- UI setup/options, a network entry with one subentry per lamp, discovery of lamps of a configured network, GATT/service-data diagnostics.
- Existing-network onboarding using NetKey/AppKey, optional DeviceKey, IV Index and a dedicated unused controller unicast address. No cloud calls, automatic provisioning or factory reset.
- Authenticated proxy filter configuration, AES-CMAC/CCM network/access traffic, DeviceKey messages, segmented access transmission/reception, complete ACKs and missing-segment retransmission.
- Serialized requests, source/opcode/value response matching, read-only query retries, report-driven state, persistent replay protection, automatic GATT reconnect with backoff.
- Crash-safe sequence ranges saved through atomic replace and file/directory fsync, outside the HA event loop. Write failures stop transmission. Sequence storage is scoped to the network/controller address, not an entity or config-entry ID.
- Conditional light with power/brightness, optional Kelvin/HS (HA also translates RGB actions), captured scenes, observed mode select and a countdown number with confirmed units/range.
- Unknown DPs remain diagnostics. No DP51–59 meanings are guessed or writable.
- HA diagnostics, EN/NL UI, HACS structure, CI and protocol/entity/session/storage tests.

Missing/unverified: forwarding between lamps over one link, automatic acquisition of mesh keys (see [docs/protocol.md](docs/protocol.md), "How users get the mesh keys"), new-device provisioning/Tuya authentication, exact Orion wire format/mode codes, CT endpoints/scaling, all scene enumeration, PIR mappings, timestamp response format, automated IV Update/recovery and key refresh. Secure beacons are authenticated; incompatible IV/key-refresh transitions block further transmission rather than guess a new state. Segmentation supports direct unicast peers, not Friend/OBO acknowledgements or virtual/group control.

## Installation

Copy `custom_components/flinq_orion` to `/config/custom_components/flinq_orion` and restart HA. Alternatively, after this repository is published, add `https://github.com/Sylvanloves1/ha-flinq-orion` in HACS → Custom repositories → Integration.

Add **FlinQ Orion** via Settings → Devices & services or discovery and enter the Bluetooth address and a name of the first lamp. This creates the network. Add more lamps later with **Add lamp** on the network entry. Without mesh keys, observation mode remains usable. The RSSI entity is created immediately. Other entities appear after authenticated reports establish the relevant fields and you select the experimental protocol. Device firmware 5.1 is the supplied profile metadata, not a firmware version independently read by this integration.

## One first hardware test (no keys required)

1. Install version **0.2.1**, restart HA, and add FlinQ Orion with `DC:23:4F:6E:A0:12`. Leave Options at `observe`.
2. Keep the lamp powered, close Smart Life completely and ensure `bluetooth-proxy-buiten` has a free active connection slot.
3. In Developer tools → Actions run:

   ```yaml
   action: logger.set_level
   data:
     custom_components.flinq_orion: debug
   ```

4. Run **FlinQ Orion: Capture Bluetooth diagnostics** (`flinq_orion.capture`), select the lamp, duration **20 seconds**. Do not operate Smart Life during capture.
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

Enter the existing NetKey/AppKey and current IV Index in the Options of the network. The DeviceKey is per lamp and optional (composition and the explicit vendor-bind action): set it with **Reconfigure** on the lamp, or import it. A NetKey can be used by one network entry only. Keys cannot be derived from normal encrypted advertisements and are never fetched from cloud by this version.

Enter a **dedicated never-used unicast controller address**, in decimal, distinct from all lamp elements and every Smart Life/gateway/controller/node address. Do not copy Smart Life's source address or pick a random address without checking the existing mesh allocation. Check the unique-source confirmation only after verification. The mesh address of each lamp is detected on its first direct connection (the existing lamp keeps **176 (`0x00B0`)**); element routing uses composition when available.

Leave unconfirmed profile switches off. CT requires proven Kelvin endpoints **and a linear Kelvin mapping**; endpoints alone do not establish scaling. HSV requires confirmed 12-character encoding. Optional mode values must have an observed enum report and confirmed meaning; `-1` leaves mode selection manual. Scenes can only replay captured DP6 blobs after explicit enablement; names `Scene 1`, etc. are capture labels, not an exhaustive firmware scene list. Countdown requires confirmed seconds and maximum.

After reload, the integration connects and performs bounded read-only discovery. It never automatically adds keys or binds models. If the vendor model requires a binding to an already installed AppKey, the advanced `bind_vendor_model` action can add that binding using the existing DeviceKey, known AppKey index and explicit `confirm: true`. This is a configuration write; do not use it as a speculative response to every timeout.

Turn the discovered light on/off and inspect both the physical lamp and the reports. No optimistic state is used. A matching report confirms protocol state, not independently observed physical light output. If no reports arrive, inspect diagnostics for NID/MIC/IV errors, proxy filter timeouts and GATT services instead of guessing values.

## Security and maintenance

HA config-entry files store keys locally without additional encryption. Password selectors mask the UI; diagnostics redact keys. Protect HA files/backups. Durable sequence/replay files contain counters and a network fingerprint, not secret keys. Do not edit/delete them. Restoring an old backup or losing cryptographic state requires a new verified unused controller address or external validated recovery; a restored counter cannot establish that intervening sequence numbers were never transmitted.

No daily Internet or Smart Life dependency exists in the command path. HA restart reloads credentials and skips unused sequence reservations. There is no reset or arbitrary packet-injection action. See [protocol findings](docs/protocol.md), [development](docs/development.md) and [test status](docs/testing.md).


## Import keys from an existing Tuya BLE Mesh entry (0.2.1)

1. Open the FlinQ Orion network → Options. Under **Import existing mesh credentials from Tuya BLE Mesh**, select the old entry of a lamp in this network and submit. Only entries with the MAC address of one of the lamps are offered.
2. The form returns with NetKey/AppKey and IV Index prefilled. The mesh address and the optional DeviceKey are stored with that lamp when you finish the options. The importer verifies available standard network/node identities and captured secure beacons; incompatible evidence rejects the import. If no verifiable evidence is available, the debug event explicitly records that verification did not occur.
3. Select a dedicated verified unused controller source address. The previous Tuya BLE Mesh controller address is shown for comparison and is blocked from reuse. Its sequence, reservation store, passwords and cloud credentials are never copied. Confirm uniqueness, keep **query_only** initially, and leave unconfirmed light metadata off.
4. Before saving active mode, disable the old Tuya BLE Mesh config entry through HA to avoid competing GATT sessions. Disable it rather than deleting its credentials. Close Smart Life, finish the options form and allow read-only discovery to run.
5. Download FlinQ diagnostics again and inspect `credential_import`, mesh/MIC errors, composition and DP reports. Power control is enabled later by selecting `vendor_dp` after a valid DP1 report (or explicitly testing SIG mode). Import is not proof that the AppKey is already bound to the vendor model.

This optional onboarding helper reads Home Assistant's config-entry API. It never imports/calls the old integration's code, modifies its entry or depends on it for daily operation. Removing the old integration after verification does not remove credentials copied into FlinQ Orion.
