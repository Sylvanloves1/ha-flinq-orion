# FlinQ Orion FQC8274 protocol

Evidence labels: confirmed (user supplied device findings), documented (protocol specification), observed (captured by this integration), candidate (unverified for this PID), unknown.

## Device findings (confirmed by supplied history, not independently tested)

FQC8274; PID `1ajecbtl`; firmware 5.1; address `DC:23:4F:6E:A0:12`; service `0x1827`; node primary unicast `0x00B0`; SIG OnOff Server `0x1000`; vendor company/model `0x07D0/0x0004`. Prior AppKey Add and OnOff bind succeeded; OnOff did not physically switch the lamp. NID 0x33 is an observation, not a constant. Earlier sequence values are not used.

## Bearer and security

SIG 0x1827 is Mesh Provisioning, 0x1828 is Mesh Proxy. Proxy Data In/Out are 0x2ADD/0x2ADE. HA selects a connectable BLEDevice from its own controller pool; bleak-retry-connector connects through that device. No local adapter requirement or independent scanner. GATT inspection must establish the actual bearer before writes are enabled. ESPHome active proxies carry BLE connections, not arbitrary mesh advertising transmission.

AES-CMAC s1/k2/k4 and AES-CCM authentication use cryptography. Managed-flooding network packets support current/previous IVI reception and MIC checking; lower transport reassembly is bounded to 16 transactions with a 20-second lifetime. It is receive-only and does not send Segment ACKs. Device-key configuration messages, virtual addresses, key refresh and authenticated IV update/recovery remain incomplete. ReplayWindow is a bounded capture deduplicator, **not** a persistent specification-compliant replay protection list for an active mesh node.

## Tuya vendor protocol (documented, device support requires capture)

Company 0x07D0 server model 0x0004, client 0x0005. Wire opcode bytes: WRITE `c9 d0 07`, WRITE_UNACK `ca d0 07`, STATUS `cb d0 07`, READ `cc d0 07`, DATA `cd d0 07`. DP command byte is 01. Query adds one-byte DP count followed by identifiers; identifier zero requests all. Writes/reports contain compact DP groups: id, type, value; raw/string/bitmap add a one-byte length. Bool/enum use one byte, value four big-endian bytes. This differs from conventional Tuya serial TLVs (two-byte lengths). Unrecognized frame types remain raw diagnostics; malformed groups never update partial state.

The vendor specification does not establish an ACK transaction ID. Do not add a guessed TID. Timestamp command 02 is documented as a category but its exact layout is not established here; preserve raw frames and do not send guessed timestamp responses.

Tuya's category documentation maps some lights to SIG OnOff, Lightness, CTL Temperature and HSL. DP2 mode has special vendor framing, DP6 compressed scene data. These category mappings do not prove the Orion behaves the same way. A failed OnOff command also does not prove every DP is vendor-controlled.

## Device DP evidence

| DP | Meaning | Range/encoding | Evidence |
|---|---|---|---|
| 1 | Power | bool candidate | confirmed identifier/meaning; wire encoding unverified |
| 2 | Mode | mapping unknown | confirmed identifier |
| 3 | Brightness | 10–1000 | confirmed range |
| 4 | Color temperature | 0–1000; Kelvin endpoints unknown | confirmed range |
| 5 | Color | HSV string candidate or SIG HSL | unknown device encoding |
| 6 | Scene | compressed format/list unknown | unknown device encoding |
| 7 | Countdown | unit/range unknown | supplied product meaning; Tuya generic lighting template instead uses music |
| 51–56, 58, 59 | Unknown | unknown | confirmed identifiers; PIR conjecture only |

Brightness scales absolute 0–1000 to 0–255 using nearest integer, clamps transmitted nonzero brightness to 10–1000. Thus 750 yields 191; the sample 189 in the request is not the result of this proportional conversion. HA brightness zero must eventually issue off, not a brightness-zero write. CT conversion stays disabled until metadata or CTL range responses establish endpoints. HSV helpers are explicitly opt-in candidates, not active capabilities.

## Sequence safety

Allocator identity must bind a unique controller source address to the NetKey context. Fresh storage is safe only for a newly allocated source never previously used in this network. Never reuse Smart Life's source address. Save an exclusive upper bound before handing out any sequence. Restart skips the unused tail. Lock serializes reservation and allocation. Failed/cancelled storage poisons the allocator until recovery. No wrap; exhaustion stops transmission. IV mismatch fails closed. Automatic IV changes are deliberately unsupported until secure beacon authentication and the specification state machine are implemented. Lost/restored-backup storage cannot guarantee safe reuse; allocate a fresh source or perform evidence-based recovery. No current sequence is hardcoded.

## Sources consulted

- [HA Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/)
- [HA Bluetooth guidance](https://developers.home-assistant.io/docs/bluetooth/)
- [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/)
- [Bluetooth SIG Mesh Protocol 1.1](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/MshPRT_v1.1/out/en/index-en.html)
- [Tuya vendor pass-through specification](https://developer.tuya.com/en/docs/iot-device-dev/tuya-sigmesh-device-vendor-model-access-standard?id=K9pikwhoo3gux)
- [Tuya category DP/model mappings](https://developer.tuya.com/cn/docs/iot-device-dev/bluetooth_software_map_mesh_data_control?id=Kd5wq9xmg0u3i)
- [Current HA Bluetooth source](https://github.com/home-assistant/core/tree/dev/homeassistant/components/bluetooth)

Independent implementation; no code copied from an existing custom integration. Research snapshot: 2026-10-13. Hardware assertions remain unverified by this build.

## Active implementation update — 0.2.0

The earlier receive-only description above describes the first 0.1.0 build. Version 0.2.0 adds the documented active GATT path, still unverified on physical Orion hardware:

- Proxy configuration uses CTL=1, TTL=0, DST=0 and the **Proxy nonce**, not a network nonce. Set Accept List Filter is followed by Add Addresses (dedicated HA source); both require authenticated Filter Status. The official sample 8.9.1 is a byte-for-byte regression test.
- Unsegmented access packets fit at most 11 plaintext access bytes; larger access messages use 12-byte encrypted transport segments, SeqZero derived from the first sequence/SeqAuth, 32-bit TransMIC and complete unicast ACKs. Missing segments retry at most twice with fresh **network** sequence values and the original upper-transport ciphertext/SeqAuth. Receive reassembly accepts out-of-order parts. Full ACKs are sent only after MIC-valid complete reassembly; partial ACK timers and OBO are not implemented. A reassembled in-progress message may complete after a newer control packet, while completed/replayed SeqAuth remains rejected after restart.
- A bounded persistent RPL records authenticated message high-watermarks before a status affects entities or request matching. A transient authorization window permits already-started segmented messages to finish without lowering the stored watermark. Live completed-segment caching re-ACKs delivery retransmissions, without replaying state updates.
- DeviceKey upper transport uses AKF=0/AID=0 and nonce type 2. Composition Get is read-only. Explicit Model App Bind binds the already installed AppKey index to the documented Tuya vendor model; no AppKey Add or bind happens automatically.
- Verified SIG opcodes: OnOff `8201/8202/8204`; Lightness `824b/824c/824e`; CTL Temperature Range `8262/8263`, Temperature `8261/8264/8266`; HSL `826d/8276/8278`. Model addresses are resolved by composition element. HSL is converted via RGB between HSV and HSL, not treated as Tuya HSV bytes. SIG TIDs cannot be reused within six seconds, including an initial post-start quiet interval. Vendor DP messages have no invented TID; matching uses source, opcode, company, key kind and expected DP/type/value. Without a documented vendor transaction identifier a delayed identical report cannot be distinguished from an immediate identical ACK.
- Tuya power/brightness writes require observed DP1 bool / DP3 value. Optional CT/HSV/countdown mappings require explicit confirmed metadata. Scenes replay only observed blobs, not guessed scene IDs. DP2 mode selection allows only observed enum values; optional white/color/scene mappings are supplied explicitly. No high-DP writes exist.
- Secure Network Beacon CMAC/Network ID are checked. A differing authenticated IV Index or Key Refresh flag blocks transmission; there is no automatic key/IV recovery. Unknown timestamp and private beacon formats remain raw diagnostics.
- Crucial persistence correction: HA Store can log and suppress a filesystem write failure. Active sequence/RPL use **DurableStore**, with private temporary file, file fsync, atomic replace, directory fsync and propagated errors in the HA executor. HA Store remains only for noncritical observation snapshots.

The firmware/profile facts at the top remain user-supplied. Adding an experimental command path does not change unknown DP51–59, CT or scene mappings into confirmed facts.

Additional primary references:

- [Espressif SIG model opcode definitions](https://github.com/espressif/esp-idf/blob/master/components/bt/esp_ble_mesh/api/esp_ble_mesh_defs.h)
- [Espressif lighting message structures](https://github.com/espressif/esp-idf/blob/master/components/bt/esp_ble_mesh/api/models/include/esp_ble_mesh_lighting_model_api.h)
- [Nordic CTL opcode definitions](https://github.com/nrfconnect/sdk-nrf/blob/main/include/bluetooth/mesh/light_ctl.h)
- [HA light entity API](https://developers.home-assistant.io/docs/core/entity/light/)
- [HA storage implementation](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/storage.py)

## Physical observation — 2026-10-13, HA 2026.10.1 / integration 0.2.0

User-supplied diagnostics contain two completed 20-second GATT captures. Observed services:

- Standard Mesh Proxy `0x1828`, Data In `0x2ADD` (write-without-response), Data Out `0x2ADE` (notify). Both capture sessions subscribed successfully and received a type-1 mesh beacon. This establishes the existing GATT proxy bearer; no mesh control has yet been tested.
- Device Information `0x180A`, Firmware Revision `0x2A26` (read), PnP ID `0x2A50` (read). Presence is confirmed; values have not yet been read by this build.
- Custom service `0x7FDD` with `0x2ADB`/`0x2ADC`; proprietary `...1912`/`...2b12` and `...7fde`/`...7fdf` services. Their meaning remains unknown. No legacy Telink authentication or writes were attempted.
- Both standard Network Identity and Node Identity advertisements were observed. Standard provisioning service `0x1827` is absent from this capture.
- The beacon has Secure Network Beacon layout, Flags=0 and IV Index=0. These are **observed, unauthenticated fields** until an existing NetKey verifies its CMAC. Network identifiers/authentication bytes are omitted from public fixtures.
- The integration is in observe mode, options contain no keys, sequence transmission is disabled, and no access/DP reports were received. That does not demonstrate a power/vendor failure: no status query was sent.
- RSSI varied from -70 to -41 dBm. Bluetooth source is redacted in the supplied diagnostics, so the exact adapter/proxy used cannot be independently identified from this file.

Next dependency: obtain the already used NetKey/AppKey (and optionally DeviceKey) locally from the earlier configured mesh implementation, plus a verified unused controller address. Do not invent keys, copy Smart Life's source address, infer keys from the public Network ID, or reset/reprovision this lamp. No further bearer capture is needed to establish 0x1828.


## Local credential import — 0.2.1

Read-only onboarding from the existing Tuya BLE Mesh config entry uses its documented fields: `mac_address`, `net_key`, `app_key`, `dev_key`, `unicast_target`, `unicast_our`, `iv_index`. String unicast addresses are hexadecimal in that integration; imported fields are normalized to integers. Only the same Bluetooth MAC is offered. No module or runtime object from the other integration is imported. This is not a wrapper dependency.

A standard Network Identity is compared to k3(NetKey). For Node Identity, IdentityKey=k1(NetKey,s1("nkik"),"id128"||01), and Hash is the last 8 bytes of AES(IdentityKey, 6 zero padding bytes || 8-byte Random || 2-byte primary unicast in big-endian). Captured secure beacons additionally validate CMAC and IV Index; key-refresh evidence blocks activation. These checks do not verify AppKey/DeviceKey or a model binding; those require authenticated mesh messages. No verifiable evidence means an unverified import, not an inferred successful authentication.

The legacy source is retained only as an exclusion and UI hint. The controller source, sequence, mesh passwords and cloud credentials are never migrated. Import defaults to query_only with source unset. A new source must be verified unused before activation; none is guessed from an incomplete view of mesh allocations.

Source for the legacy field schema: [Tuya BLE Mesh constants](https://github.com/11z4t/tuya-ble-mesh/blob/main/custom_components/tuya_ble_mesh/const.py), [SIG provisioning entry storage](https://github.com/11z4t/tuya-ble-mesh/blob/main/custom_components/tuya_ble_mesh/config_flow_sig.py). These were studied only as a documented data layout; no implementation was copied.
