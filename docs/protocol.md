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

## Authenticated physical discovery — 2026-10-14 / 0.2.1

The next user diagnostics confirm successful Proxy Filter Status, Secure Network Beacon authentication (IV Index 0, no IV Update or Key Refresh), DeviceKey-decrypted segmented Composition Data Status with complete transport ACK, and AppKey-decrypted vendor DATA from the lamp. Controller address 2 is newly assigned by the user for this single-device network. NID 0x33 is now derived from the imported NetKey and matches authenticated traffic; it remains derived, not a constant.

Composition Page 0 has one element at the supplied primary address, 15 SIG models, company 0x07D0, product 0x0300, version 0x3135, CRPL 105 and features 7. SIG models include OnOff 0x1000, Lightness 0x1300, CTL 0x1303, CTL Temperature 0x1306 and HSL 0x1307. Vendor model is 0x07D0/0x0004. A sanitized decoded fixture contains only model metadata and no encrypted traffic, addresses, identifiers or keys.

The all-DP READ received two successfully decrypted DATA replies with command byte 01 and no DP groups. In 0.2.1 the query matcher requires nonempty data, so it incorrectly presents these empty responses as a timeout and retries. The local correction records `empty_dp_report` and does not invent capabilities or retry a received empty reply. This establishes AppKey reception/response but does not establish that any DP write works or that all SIG models are bound to that AppKey.

Next physical experiment uses the already implemented SIG read-only startup queries in `sig` mode. This is motivated by the actual composition and Tuya's published lighting mappings; it is not a claim that Generic OnOff physically works. No model-bind or power writes are required for this read-only experiment.

## Physical SIG status discovery — 2026-10-14 / 0.2.1

After switching to SIG mode, the lamp returned authenticated OnOff Status (`01 01 00`), Lightness Status (`99 99 99 99 01`), CTL Temperature Status (`20 21 00 00 20 21 00 00 00`) and HSL Status (`cc 4c 00 00 ff ff 84`). Current protocol state decodes to OnOff=true, Lightness Actual=39321 (60%), CTL temperature=8480 and HSL=(19660,0,65535). These are received model states, not an independently verified physical power/color observation. The HSL lightness/hue/saturation convert to HSV red at about 60% brightness; this does not establish the active Tuya work mode or DP5 vendor encoding.

CTL Temperature Range Get still timed out. Do not interpret reported 8480 as a measured physical Kelvin value or infer 800–20000 as the lamp's real white-temperature range. CT remains unadvertised as an HA light capability until the range/scaling evidence exists.

A temporary initial DeviceUnavailableError was followed by a successful reconnect. Sequence reservation survived the options reload: the controller skipped the previous unused reservation tail and used fresh sequence values from the next block. The counter figures in these captures remain observations, not implementation defaults.

Next experiment: use the discovered light entity to send only OFF and ON, observing physical output and corresponding reports. No vendor configuration/model-bind writes are indicated by the current successful SIG read responses.

One-byte vendor DATA `01` remains semantically ambiguous: the pass-through decoder yields no DP groups, while Tuya's lighting documentation separately describes a single-byte lighting-mode payload. A mode interpretation is only a candidate, not a confirmed DP2 enum or a reason to send a guessed mode write. The timeout correction acknowledges receipt without assigning either meaning to a new entity.

## Responsiveness correction (0.2.2)

The second HA capture and user test confirm physical SIG power control. Once
transmitted, acknowledged commands returned in approximately 0.1–0.25 seconds.
An unanswered CTL Temperature Range Get was retried after 15 seconds and held
the serialized command queue for 30 seconds. Discovery reads now wait at most
2 seconds each, without retries. Unsupported discovery queries do not count as
control failures. Commands retain their normal response timeout and no write retries.
An empty vendor response completes discovery without inferring DP capabilities.

All SIG light SET messages now include Transition Time 0 and Delay 0, rather
than using the node's Default Transition Time when these optional fields are
omitted. Reported remaining-time fields in the capture were nonzero. This change
requests immediate application; physical timing must be verified on the lamp.
See [Nordic CTL API](https://github.com/nrfconnect/sdk-nrf/blob/main/include/bluetooth/mesh/light_ctl.h)
and [ESP-IDF generic model definitions](https://github.com/espressif/esp-idf/blob/master/components/bt/esp_ble_mesh/api/models/include/esp_ble_mesh_generic_model_api.h).
Parameter changes skip redundant power-on writes when the connected lamp already
reports on; an explicit power-on action still sends a command.

Update to 0.2.2 and restart HA, keeping the existing entry, keys, controller address
and sequence storage. Change brightness/color after startup and compare response
time. Re-pairing or deleting sequence storage is unnecessary and unsafe.

## Idle availability correction (0.2.3)

A live GATT session no longer expires solely because the node has sent no access
status for five minutes. Mesh nodes need not send periodic reports. An established
connection keeps reachability available, while command failures can still mark
the device unresponsive. A disconnected silent device expires normally. No polling
was added. This is a confirmed code defect; fresh diagnostics are required to
establish whether it explains the user's unavailable lamp after the update.

## Connection failure investigation (0.2.4)

HA Bluetooth diagnostics show the sole ESPHome scanner `bluetooth-proxy-buiten`
with 3 slots, 3 free, no allocations or in-progress connections, and four failed
connections to the Orion. The lamp was received at -41 dBm. The retry library's
`BleakOutOfConnectionSlotsError` therefore does not establish that other devices
occupy slots. Its underlying exception message was lost in integration diagnostics.
Version 0.2.4 records up to four bounded exception causes and disconnection events,
with configured keys and Bluetooth addresses redacted. No mesh keys, controller
addresses, provisioning or sequence stores are changed during this investigation.

Install 0.2.4 and restart HA. Wait five minutes for a connection attempt and download
FlinQ Orion diagnostics. Inspect `connection_error.causes` to distinguish API slot
waiting, controller errors, and missing-device errors before changing proxy settings.

## SIG startup isolation (0.2.5)

Capture 6 establishes a successful proxy filter and authenticated composition
response, followed by vendor query opcode 0xCC, no vendor reply, a Generic OnOff
Get two seconds later and GATT disconnect approximately 0.45 seconds after that.
Earlier captures also show vendor queries near disconnections. Causation is not
yet established. SIG startup now excludes experimental vendor queries, obtains
standard light status first, and puts optional range/composition discovery last.
Explicit vendor queries remain available for controlled experiments. This change
requires physical verification; it does not alter cryptographic state or provision.

## Automatic startup read boundary (0.2.6)

Captures 7 and 8 show the same session: power, lightness, CTL temperature and HSL
status each respond within roughly 0.1 seconds. Temperature Range Get then times
out, composition is attempted and the connection drops. This disproves attributing
all disconnects to the vendor query. Automatic SIG discovery now sends only those
four responding GETs. Range/composition requests are available solely through
`flinq_orion.query_status` with explicit `include_optional: true`. The normal query
button remains limited to the startup reads. This isolates optional queries; it
does not establish their causal role or guarantee physical connection stability.

Update to 0.2.6, restart HA, and power-cycle the lamp once to reproduce its
connectable advertisement window. Leave it untouched for 60 seconds, then try
power on/off. Download diagnostics after the attempt, including if it succeeds.
If disconnection persists without optional traffic, investigate the ESPHome
connection reason and the lamp's proxy advertising behavior. Cached RSSI reflects
received advertisements, not proof of a working GATT connection.

## Proxy log and service cache (0.2.7)

The ESPHome log reports `Connecting v3 with cache`, connection open, then a local
`Disconnecting` call approximately 5.1 seconds later and DISCONNECT_EVT reason
0x16. It does not establish that the lamp initiated the disconnect. A stale GATT
cache is a candidate explanation for the earlier handle-30 GATT error, not proven.
Connections now request service discovery with `use_services_cache=False`. Failed
GATT setup clears the selected client's service cache through the supported
BleakClientWithServiceCache API before disconnecting. It never clears mesh state.
Diagnostics now include setup stage and characteristic/descriptor handles.
See [bleak-esphome cache documentation](https://bleak-esphome.readthedocs.io/en/latest/usage.html)
and [retry connector source](https://github.com/Bluetooth-Devices/bleak-retry-connector).

Install 0.2.7, restart HA and power-cycle the lamp once. Wait one minute and try
on/off. Capture FlinQ diagnostics and simultaneous ESPHome logs to establish
whether service discovery, notification subscription or proxy filter setup fails.

## Correlated capture 12 / proxy log 3 (2026-10-14)

At 12:52:19 local time the proxy opens an uncached connection, discovers services
and reports MTU 250 and a medium connection-parameter update. HA enters notification
subscription at 10:52:19 UTC. Mesh Proxy Data Out 0x2ADE has handle 28; its CCCD
0x2902 has handle 30. At 12:52:40 the proxy reports an error writing descriptor
0x1E (30), status 133, immediately followed by disconnect reason 0x08 (connection
timeout). No mesh access or proxy filter message was transmitted. Fresh discovery
did not resolve the failure; the CCCD handle is consistent with the discovered table.

This establishes failure during GATT notification subscription, not a mesh key,
sequence or DP response failure. It does not distinguish radio coexistence,
connection-parameter incompatibility, proxy/stack behavior or peripheral behavior.
Earlier vendor/range-query correlations are insufficient evidence of causation.
Do not promote 0.2.7 as a physically validated stable release. Inspect the actual
ESPHome YAML before changing radio or connection parameters. No additional code
release was made from this capture.

Reference: [ESP-IDF HCI error definitions](https://github.com/espressif/esp-idf/blob/master/components/bt/host/bluedroid/stack/include/stack/hcidefs.h)
define HCI_ERR_CONNECTION_TOUT as 0x08.

## Correlated capture 14 / proxy log 5

After the WiFi power-save experiment and manual status action, at 13:18:19 local
time the uncached proxy connection opens (MTU 250, medium connection parameters).
Notification subscription and authenticated proxy filtering succeed. Four standard
SIG GETs all receive authenticated replies by 13:18:20.800. At 13:18:22.951 the
proxy reports DISCONNECT_EVT reason 0x08 without a preceding local Disconnecting
request. No optional range/composition/vendor query is transmitted in this session.

This establishes a link timeout even after successful startup and disproves
optional queries as a necessary cause. It does not identify which endpoint,
connection-parameter negotiation or radio conditions cause the timeout. Do not
claim that WiFi power-save adjustment, cache refresh or query removal resolved it.
Next physical isolation: keep software/settings fixed, temporarily place the
proxy 1–2 metres from the lamp with unobstructed placement, and reproduce one
manual status connection with concurrent logs. Strong advertisement RSSI alone
does not demonstrate sustained bidirectional link reliability.

## Connection parameter source audit (ESPHome 2026.9.1)

Source audit confirms uncached Bluedroid setup requests FAST min/max 6 units
(7.5 ms), latency 0, supervision timeout 1000 units (10 s). Discovery completion
calls an update to MEDIUM min 7/max 9 (8.75–11.25 ms), latency 0, timeout 800 (8 s).
These are requested parameters, not measured negotiated values. A disconnect
roughly 3.1 s after open cannot by itself be attributed to the requested 8 s timer.

`esp32_ble/ble.cpp` handles `ESP_GAP_BLE_UPDATE_CONN_PARAMS_EVT`: failed updates
are WARN; successful updates are VERBOSE under tag `esp32_ble` with actual interval,
latency and timeout. Previous DEBUG captures omit successful negotiated values.
Use existing firmware logging instrumentation before introducing an interval override.
The HA client exposes `set_connection_params`; the ESPHome backend requests the
update through the API, and unsupported firmware may only warn and return. A
completed API call alone therefore must not be treated as proof of negotiation.
No automatic parameter override or published integration change was made.

Sources (version pinned where applicable):
- https://github.com/esphome/esphome/blob/2026.9.1/esphome/components/ble_device_base/ble_client_state.h
- https://github.com/esphome/esphome/blob/2026.9.1/esphome/components/bluetooth_connection/bluetooth_connection_bluedroid.cpp
- https://github.com/esphome/esphome/blob/2026.9.1/esphome/components/esp32_ble/ble.cpp
- https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/src/bleak_esphome/backend/client.py

Next capture: compile logger at VERBOSE, initial runtime DEBUG, override only
esp32_ble to VERBOSE. Keep radio, mesh and integration settings fixed. Power-cycle
the lamp once with live logs, invoke normal status button once advertisements
return, capture two minutes and inspect `Conn params updated`/`Conn param update
failed` together with the connection-open and disconnect timestamps.

## Negotiated values from proxy log 7

At 13:47:51.819 the successful GAP update reports interval 9 (11.25 ms),
latency 0, supervision timeout 800 (8 s). At 13:47:52.737 another successful
update reports interval 32 (40 ms), latency 0, timeout 200 (2 s). Disconnect
reason 0x08 follows at 13:47:55.206, 2.469 seconds after the second update.
The shorter negotiated timer is now observed, not inferred from defaults. The
log does not identify which endpoint initiated the second update; attributing
it to the lamp is a hypothesis. The timing supports a parameter/link interaction
but does not prove that increasing supervision timeout restores reliable traffic.

A next isolated experiment may preserve the observed 40 ms interval and latency
0 while requesting timeout 800 (8 s) through the supported HA BLE client API.
Verify the accepted GAP values, not merely API completion, and sustained on/off
responses over at least 60 seconds. Longer availability alone is insufficient:
the link may simply time out later. No automatic override is enabled in 0.2.7.

## Beta experiment result: capture 15 / proxy log 8

Two experiment calls at 14:14:30 and 14:17:15 local return from the HA API,
but each corresponding proxy GAP event reports `Conn param update failed,
status=16`. No accepted 40 ms / 8 s update is reported. Notification CCCD writing
then fails with status 133 and disconnect reason 0x08. No authenticated mesh
reports are received in these attempts. The beta is not a successful fix and
must not be promoted to main. Status 16 is retained numerically; do not claim
a rejection by the lamp or assign an HCI meaning without tracing the API enum.

A control with a separate BLE client (integration disabled to avoid competition)
can test sustained GATT connection and Proxy Data Out notification subscription
without keys, provisioning, descriptor overrides or mesh writes. This separates
proxy-path compatibility from peripheral-side behavior. Device keys need not be
shared with the control client. No further parameter guesses are justified by
this unsuccessful experiment.

## Source audit and capture correlation (0.2.9b1)

All times are UTC. Proxy logs show local time (UTC+2) and are converted here.

### Facts from source code

- ESPHome 2026.9.1, `bluetooth_connection_bluedroid.cpp`: an uncached connection uses
  FAST parameters (7.5 ms, 10 s), runs service discovery and requests MEDIUM
  (8.75–11.25 ms, 8 s) after discovery. A cached connection opens with MEDIUM, runs no
  discovery and sends no parameter update (`bluetooth_connection_hub.cpp` asks for MEDIUM
  again; Bluedroid logs "connection parameter already set").
- The MTU request is sent on `ESP_GATTC_CONNECT_EVT` for both connection types. A cached
  connection reports `mtu=23` by design (`DEFAULT_ATT_MTU`, OPEN_EVT handler). The
  "MTU 23 versus 250" difference in earlier notes is a reporting difference, not a
  negotiated difference.
- bleak-retry-connector 4.7.1: `use_services_cache=False` sends
  `dangerous_use_bleak_cache=False`. bleak-esphome 4.0.0 then always requests
  `V3_WITHOUT_CACHE`. With the default (`True`), bleak-esphome requests `V3_WITH_CACHE`
  when it holds services and an MTU for the address. That cache is in memory per proxy API
  session. A Home Assistant restart or a proxy reconnect clears it. An integration cannot
  fill it (`set_cached_services` is a no-op).
- 0.2.7 called `clear_cache()` after every failed setup. That forced every next attempt
  onto the uncached path.
- `Conn param update failed, status=16`: `esp_bt_status_t` value 16 is
  `ESP_BT_STATUS_TIMEOUT`, mapped from `HCI_ERR_HOST_TIMEOUT` (`btc_hci_to_esp_status`).
  Bluedroid sets it when its connection-update timer expires (`l2c_process_timeout`,
  `BTU_TTYPE_L2CAP_UPDA_CONN_PARAMS`). `CalConnectParamTimeout` gives 1–2 s here. The
  controller did not report completion. It is not a rejection by the lamp.
- Telink SIG Mesh SDK builds from 2020–2022 call
  `bls_l2cap_requestConnParamUpdate(16, 32, 0, 200)` in `mesh_ble_connect_cb`. The lamp
  exposes the Telink OTA service UUID. The observed update to interval 32, latency 0,
  timeout 200 matches this request. ESPHome never requests these values. Conclusion: the
  40 ms / 2 s update is very probably requested by the lamp. The proxy log does not show
  the L2CAP request itself.
- habluetooth `base_scanner.py` merges service UUIDs and service data of successive
  advertisements. One Unprovisioned Device packet therefore stays visible in
  `service_data` until Home Assistant restarts. The raw packet shows the latest content.

### Links opened by the proxy (logs 1, 3, 5, 7, 8)

| Log | Type | Open | Lifetime | End | Parameter updates (seconds after open) |
|---|---|---|---|---|---|
| 1 | cached | 10:39:26 | 5.1 s | local (no Proxy Filter Status in 5 s) | none logged (DEBUG level) |
| 3 | uncached | 10:52:20 | 20.1 s | 0x08, CCCD write status 133 | none logged (DEBUG level) |
| 5 | uncached | 11:18:20 | 3.2 s | 0x08 | none logged (DEBUG level) |
| 7 | uncached | 11:47:52 | 3.5 s | 0x08 | 9/800 at 0.11, 32/200 at 1.03 |
| 8 | uncached | 12:14:28 | 9.9 s | 0x08 | 9/800 at 1.12, beta request failed at 3.69 |
| 8 | uncached | 12:15:08 | 3.1 s | local (no 0x1828 in table) | 9/800 at 1.55, 32/200 at 2.17 |
| 8 | uncached | 12:15:54 | 10.6 s | 0x08 | none |
| 8 | uncached | 12:17:13 | 9.9 s | 0x08 | 9/800 at 2.58, beta request failed at 5.00 |

No uncached link lasted longer than 21 s. Lifetimes near 10 s match a link that stopped
early under the 8 s or 10 s supervision timeout. Lifetimes near 3 s match the 2 s timeout
after the 32/200 update. The link loss is therefore not always tied to that update.

### Lamp state across captures

- Working 0.2.1 sessions (08:49, 08:55, 09:04): the lamp advertised Network ID.
- Every failing session with advertisements (09:25 onwards): Node Identity.
- 12:15:09: a fresh discovery returned the Telink provisioning layout: 0x1827 visible,
  the proxy characteristics under 0x7FDD. HA recorded Unprovisioned Device service data
  from 12:15:12. The 12:17:14 discovery returned the normal proxy layout again.
- The last authenticated mesh message was received at 11:18:20.
- The Node Identity random changed at intervals of about 181 s in capture 13. The cause
  is unknown.
- Diagnostics `data.version` was hard-coded. Captures 3, 4 and 5 report 0.2.1 there,
  but `integration_manifest.version` shows 0.2.2 and 0.2.3. 0.2.9b1 reads the manifest.

### What this does and does not explain

The 0.2.2 regression did not come from connection code: 0.2.1 and 0.2.2 have identical
GATT code. Two hypotheses remain:

- H1, lamp state: the lamp changed state between 09:04 and 09:25 and has dropped links
  since, independent of the integration.
- H2, connection path: the uncached proxy path fails with this lamp and the cached path
  does not.

H2 is supported by the long 0.2.1 sessions, which were probably cached (not observed).
H2 is weakened by the second 0.2.2 connection at 09:26:56. That connection was probably
cached and stopped within about 4.6 s. Log 1 is the only observed cached link, and the
lamp did not answer the proxy filter on it. Neither hypothesis is proven.
[link-test-procedure.md](link-test-procedure.md) separates them in one test.

Sources:
- https://github.com/esphome/esphome/blob/2026.9.1/esphome/components/bluetooth_connection/bluetooth_connection_bluedroid.cpp
- https://github.com/esphome/esphome/blob/2026.9.1/esphome/components/bluetooth_connection/bluetooth_connection_hub.cpp
- https://github.com/esphome/esphome/blob/2026.9.1/esphome/components/esp32_ble/ble.cpp
- https://github.com/espressif/esp-idf/blob/release/v5.5/components/bt/host/bluedroid/api/include/api/esp_bt_defs.h
- https://github.com/espressif/esp-idf/blob/release/v5.5/components/bt/host/bluedroid/btc/core/btc_util.c
- https://github.com/espressif/esp-idf/blob/release/v5.5/components/bt/host/bluedroid/stack/l2cap/l2c_ble.c
- https://github.com/espressif/esp-idf/blob/release/v5.5/components/bt/host/bluedroid/stack/l2cap/l2c_main.c
- https://github.com/zhou-peter/Telink_SIG_Mesh_ADS50_LIGHT (mesh_common.c, `mesh_ble_connect_cb`)
- https://github.com/telink-semi/tc_ble_mesh (current SDK: request 16, 32, 0, 500)
- bleak-esphome 4.0.0 and bleak-retry-connector 4.7.1 source distributions (PyPI)
- https://support.tuya.com/en/help/_detail/K9hut3w10nby8 (three toggles reset lighting products)

## Test result 0.2.9b1: captures 16/17, proxy log 9 (2026-10-14)

Procedure: [link-test-procedure.md](link-test-procedure.md). Times are UTC.

### Passive network check

`network_evidence` was `node_identity_match` in 4 of 4 checks, and no Unprovisioned
Device packet was seen. The lamp holds our NetKey and advertises unicast address 0x00B0.
Two probe reads at 13:46:56 received authenticated replies, so the NetKey and AppKey still
work. Re-onboarding is not indicated by this evidence.

### Links

| Link | Type | Open | Lamp update 32/200 | Last lamp traffic | Loss | Lifetime |
|---|---|---|---|---|---|---|
| gen 1 | not in proxy log (probably uncached, first after restart) | 13:40:51 | not logged | notifications at +1.4 s | during proxy filter setup | 5.1 s |
| gen 3 | cached | 13:43:54 | +1.33 s | none after the update | 0x08; CCCD write failed at update +1.98 s | 6.7 s |
| gen 4 | cached | 13:46:56 | +1.06 s | notification at +0.51 s | 0x08 at update +2.27 s | 3.4 s |

Probe summary: 9 reads, 2 responses (both on gen 4 within 0.51 s of the link opening),
longest link 0.51 s at a response. Between links the lamp was unreachable for minutes
(connect timeouts, "last advertisement 255 s / 339 s ago").

### Evaluation against the procedure

Cached and uncached links both stopped within 21 s. By the criterion fixed before the
test, H2 (the uncached proxy path is the cause) is rejected. The connection policy of
the integration does not explain the failure.

### Pattern now observed three times

In every link where the proxy log shows both the lamp-requested update to interval 32
(40 ms), latency 0, timeout 200 (2 s) and a later loss (log 7, log 9 gen 3, log 9 gen 4),
the loss occurred 2.0–2.5 s after the update. No lamp traffic is logged after the update.
At 11.25 ms the same links carried notifications and authenticated replies. Two cached
and one uncached link show this, so the pattern does not depend on the connection type.

This is consistent with a loss of synchronisation at the update instant. The data does
not show which side fails. Other losses (log 8) happened before the lamp update, so the
update is not shown to be the only failure point.

### Lamp sequence numbers

The lamp's mesh SEQ went from 657 (09:04:53, last working session) to 683 (09:25:58, first
failing session), and from 985 (11:18:20) to 1461 (13:46:55). The Telink reference SDK
adds 0x80 to the saved SEQ at boot (`MESH_CMD_SNO_SAVE_DELTA`). Assuming the lamp firmware
does the same (not verified), the lamp did not reboot between the last working and the
first failing session. The 476 increase after 11:18 fits several reboots, which matches
the power cycles during the beta test.

### Remaining explanation

H1 remains: since about 09:25 the lamp loses links, most visibly at its own parameter
update, independent of the integration. Unknown: whether the lamp alone causes this, or
the interaction between this lamp and the ESP32 controller. A connection from a different
central (an iPhone, without keys and without mesh writes) separates these two.
