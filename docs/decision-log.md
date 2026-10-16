# Decision log

Decisions are numbered and never rewritten. A later decision that replaces an earlier one
refers back to it.

## D-001 Recovery strategy after the failed 0.2.8b1 beta (2026-10-14)

**Context.** 0.2.1 kept working links for at least 43 s. From 0.2.2 on, links stopped after
seconds and the lamp often became unreachable. The 0.2.8b1 connection-parameter beta
failed. A series of single-hypothesis releases had not found the cause.

**Considerations.**
- 0.2.1 and 0.2.2 have identical GATT connection code.
- Every uncached ESPHome proxy link in the logs (7 links) stopped within 21 s. One cached
  link was logged. It was closed by Home Assistant after 5.1 s without a link loss, but
  the lamp did not answer the proxy filter.
- 0.2.7 forced the uncached path (`use_services_cache=False`, cache clear after every
  failure). The 0.2.1 sessions were probably cached (inferred, not observed).
- The lamp changed from Network ID to Node Identity advertising when the failures began.
  It showed a provisioning GATT table once. Its current provisioning state is unknown.
- Details: [protocol.md](protocol.md), "Source audit and capture correlation (0.2.9b1)".

**Options.**
- A. Passive network check, the 0.2.1 cache policy, connection instrumentation and one
  test procedure, as prerelease 0.2.9b1 from main. Gives: separates lamp state (H1) from
  connection path (H2) in one test; checks provisioning without a connection. Costs: code
  and a test; does not repair the lamp if H1 is true.
- B. Test the existing v0.2.1 reference prerelease without changes. Gives: no code change,
  exact reference. Costs: no network check, no link lifetimes; H1 and H2 stay mixed.
- C. Only the passive network check. Gives: smallest change. Costs: one more round before
  any change to the connection.

**Recommendation.** A, because it is the only option that can reject or support H2 in one
test, and it checks first whether the lamp still belongs to the network.

**Decision.** A, chosen by Sylvan Loves. Status: settled.

## D-002 When to clear the GATT service cache (2026-10-14)

**Context.** Part of D-001. 0.2.7 cleared the cache after every failed setup.

**Considerations.** A cleared cache makes the next connection uncached. Every logged
uncached link failed. A cached table can be wrong when the lamp changes its GATT layout,
as at 12:15 UTC (provisioning layout without 0x1828).

**Options.**
- Never clear (0.2.1). Gives: always cached after the first connection. Costs: a cached
  table without 0x1828 stays until Home Assistant restarts.
- Clear after every failure (0.2.7). Gives: fresh tables. Costs: forces the failing path.
- Clear only when the table has no Mesh Proxy service. Gives: cached path after link loss,
  and recovery from a wrong table. Costs: a wrong table that still contains 0x1828 is not
  detected.

**Recommendation.** Clear only when the table has no Mesh Proxy service.

**Decision.** Clear only when the table has no Mesh Proxy service, approved with D-001 by
Sylvan Loves. Status: settled.

## D-003 Keep the 0.2.2–0.2.6 application changes (2026-10-14)

**Context.** Part of D-001. A pure 0.2.1 baseline would also undo the latency fix.

**Considerations.** Links also stopped with no mesh traffic at all (capture 12) and with
only four answered status reads (capture 14). The 0.2.2 change removed a proven 30 s
command-lock delay.

**Options.** Keep them (gives the latency fix; costs a small difference from the 0.2.1
baseline), or revert them (gives an exact baseline; brings back the 30 s delay).

**Recommendation.** Keep them. The evidence does not implicate them.

**Decision.** Keep them, approved with D-001 by Sylvan Loves. Status: settled.

## D-004 Diagnostics version from the manifest (2026-10-14)

**Context.** Found during the D-001 audit. `data.version` in diagnostics was a hard-coded
string. Captures 3–5 reported 0.2.1 while 0.2.2 and 0.2.3 ran.

**Considerations.** Wrong version labels mislead every capture comparison.

**Options.** Read the version from the integration manifest (gives one source of truth;
costs one loader call), or keep a manual string (costs repeated mistakes).

**Recommendation.** Read the version from the manifest.

**Decision.** Implemented in 0.2.9b1. Status: open, awaiting review by Sylvan Loves.

## D-005 Evaluation of the 0.2.9b1 link test (2026-10-14)

**Context.** D-001 defined one test with fixed criteria to separate H1 (lamp state) from
H2 (uncached proxy path).

**Considerations.** Two cached links (proxy log 9) and one probably uncached link stopped
within 7 s. Network evidence matched our NetKey and address 0x00B0. In three of three
logged cases the link was lost 2.0–2.5 s after the lamp-requested update to 40 ms / 2 s.
Details: [protocol.md](protocol.md), "Test result 0.2.9b1".

**Options.**
- Accept the criterion: H2 is rejected; the next step concerns the lamp or the radio link,
  not the integration's connection policy.
- Repeat the test. Gives: more samples. Costs: more power cycles and time; the criterion
  was met clearly.

**Recommendation.** Accept the criterion. Keep the 0.2.9b1 cache policy, because it is the
0.2.1 policy and it does not make the failure worse.

**Decision.** Recorded as the evaluation of the agreed criterion. The next step is open
and needs a decision by Sylvan Loves. Status: open.

## D-006 Next step: control link from a Mac with PacketLogger (2026-10-14)

**Context.** D-005 rejected H2. Open question: does the lamp lose the link by itself, or
only with the ESP32 proxy as central?

**Considerations.** A second central separates these two without keys or mesh writes.
An iPhone shows only the link duration. A Mac with PacketLogger also shows the parameter
update request, the accepted parameters and the disconnect reason. Reports exist of
PacketLogger capturing nothing on recent macOS; the script output still answers the main
question in that case.

**Options.**
- A. iPhone with nRF Connect. Gives: quick. Costs: no parameters visible.
- B. Mac with PacketLogger. Gives: parameters and disconnect reason. Costs: Additional
  Tools for Xcode, a logging profile and more setup.
- C. Other central hardware (ESP32-C3/S3 proxy, USB adapter). Gives: tests a possible
  solution. Costs: hardware and time.
- D. Reset and re-onboard the lamp. Gives: a clean lamp state. Costs: irreversible, new
  keys, new controller setup.

**Recommendation.** A, as the cheapest discriminating test.

**Decision.** B, chosen by Sylvan Loves. Status: settled. The central is a read-only
script (`tools/control_link_test.py`) instead of a GUI app, so that every event has a UTC
time for correlation with PacketLogger.

## D-007 Response to a probable lamp-side fault (2026-10-14)

**Context.** With no Home Assistant integration active and no connection attempts, the lamp
advertised for under 10 s every 181 s, with a new Node Identity random each time. A Mac got
no link in three clean attempts. See [protocol.md](protocol.md), "Passive monitor 2".

**Considerations.** The integration and the ESP32 are not needed for the failure. A boot
loop is the most consistent explanation, but it is an inference. A factory reset changes
the NetKey, AppKey and DeviceKey and needs new onboarding.

**Options.** Observe one power cycle physically and passively; leave the lamp unpowered for
longer before a cold start; factory reset and re-onboard; contact the seller or FlinQ.

**Recommendation.** First the physical observation of one power cycle, because it can
confirm or reject the boot loop without changing the lamp.

**Decision.** Open, awaiting Sylvan Loves.

## D-008 Observe one power cycle (2026-10-14)

Settles D-007. **Decision:** observe one power cycle physically, with a passive Mac monitor,
before any reset. Chosen by Sylvan Loves. Status: settled.

## D-009 Connection strategy for a battery lamp (2026-10-14)

**Context.** The Orion runs on a 2200 mAh Li-ion battery charged by solar or USB-C. With a
low battery it enters a cycle of about 180 s in which no link works. After charging, one
persistent link was stable for more than 8 minutes, with 0.16–4.4 s response time.

**Considerations.**
- Response with a persistent link: 0.2–1.3 s, sometimes up to 4.4 s. A new link took
  0.8–3 s plus 0.3–0.9 s setup when the lamp advertised normally.
- The extra energy of a persistent link is not measured. It is probably small compared
  with the lamp's own mesh listening (relay, proxy and friend supported), but that is an
  estimate.
- The lamp has no battery data point. A low battery is only visible indirectly.
- The proxy filter accepts only messages to our address, so the lamp's own status changes
  (for example by PIR) do not reach Home Assistant in either mode.
- A persistent link occupies one of three ESP32 connection slots.

**Options.**
- Persistent with low-battery detection. Gives: fastest response. Costs: one slot; unknown
  extra energy.
- Connect per command, disconnect after idle time. Gives: no idle link, free slot.
  Costs: 1–4 s extra on the first command; state can be stale; up to about 3 minutes or
  failure when the battery is low.
- Persistent, unchanged. Gives: no work. Costs: retries during low battery, no warning.

**Recommendation.** Persistent with low-battery detection.

**Decision.** Connect per command, chosen by Sylvan Loves. Status: settled. Details of
idle time, refresh and failure behaviour: D-010.

## D-010 Details of on-demand connections (2026-10-14)

**Context.** Follows D-009. Without a link most of the time, entity availability can no
longer require a connected link; that change is not a choice. Four details were open.

**Considerations.** Automations often send several commands within seconds. A new link
costs 1–4 s. The lamp does not report its own changes to Home Assistant. With a low
battery the lamp is reachable for a few seconds every ~180 s.

**Options and decisions** (all chosen by Sylvan Loves, status settled):

| Detail | Options | Recommended | Decision |
|---|---|---|---|
| Idle time before disconnect | 30 s, 60 s, 5 min | 60 s | 60 s |
| Status refresh | on command and at start, every 30 min, every hour | on command and at start | every 30 min |
| Unreachable at a command | error after ~20 s, wait up to ~3.5 min | error after ~20 s | error after ~20 s |
| Mode configurable | option with on-demand default, on-demand only | option | option, default on-demand |

The 30 minute refresh costs one link per refresh (48 per day) and fails while the battery
is low; this cost was accepted for a more current state.

## D-011 How to add the lamp functions (2026-10-14)

**Context.** The user asked for every controllable function and every sensor, including
the battery. The Tuya cloud schema lists 15 data points (DP 1–7, 51–56, 58, 59) and no
battery data point. Only power is proven physically. Vendor DP reports were never received;
they probably go to another destination that the proxy filter drops.

**Options.**
- Phased with evidence: read first, then entities, writes per DP after a write and
  readback test. Gives: no unintended changes. Costs: two or three test rounds.
- All from the schema, read-only until a write test. Gives: everything visible fast.
  Costs: values can be wrong if the encoding differs.
- All from the schema, including writes. Gives: fastest. Costs: a wrongly encoded write
  can change PIR, standby or light settings without a way to read them back.

**Recommendation.** Phased with evidence.

**Decision.** All from the schema, including writes, chosen by Sylvan Loves. Also: a derived
"battery probably low" sensor, and 0.3.0b1 is released together with this work. Status:
settled.

## D-012 Design details for the schema functions (2026-10-14)

**Context.** Implementation of D-011. These details were chosen during the build and are
recorded for review.

| Detail | Choice | Reason | Alternative |
|---|---|---|---|
| Proxy filter | Reject list with no entries (accept every destination), per link | The lamp publishes DP reports to another address (2 of 2 captures) | Add guessed group addresses; fails if the guess is wrong |
| Foreign destinations | Read and use as lamp state; acknowledge only messages to us; only replies to us or to a group complete a request | Group reports carry the state; a reply to another controller is not our answer | Drop them (no reports) |
| Transport | SIG for power, brightness and colour; Tuya DPs for settings, white mode and colour temperature | SIG is proven to answer; settings exist only as DPs | Everything over DPs |
| Colour temperature | DP 4 with 2000–6500 K, linear, unless SIG range or confirmed endpoints exist | The lamp never answered CTL Range Get; HA's Tuya integration used 2000–6500 K for this lamp | SIG CTL with an unknown scale |
| Music mode | Not offered | Needs audio data from a phone app | Offer and leave it inactive |
| DP 52 pir_state | Enum sensor, not a motion sensor | "pir"/"none" meaning is not confirmed | Motion binary sensor |
| Battery warning | On after 3 Node Identity randoms within 15 min without a Network ID; off on a Network ID | Matches all observations of 2026-10-14 | No warning |
| Old options | Mode codes and countdown confirmation removed | Superseded by the schema | Keep unused options |
| SIG discovery | Adds one vendor DP query, timeout 5 s | 0.2.5 removed it on a suspicion that the battery result disproved | Leave it out (no settings) |

**Decision.** Implemented in 0.3.0b1. Status: open, awaiting review by Sylvan Loves.

## D-013 Fewer entities and a battery status (2026-10-14)

**Context.** After installing 0.3.0b1 the user reviewed the device page. The light mode
select duplicates the colour modes of the light entity, the PIR state showed "unknown",
and the configuration settings were not wanted.

**Decision** (Sylvan Loves, settled; supersedes the entity scope of D-011 and the DP 52,
music mode and old options rows of D-012):

- Remove the light mode select (DP 2), the PIR state sensor (DP 52) and all configuration
  entities: control mode, light sensor threshold, motion sensitivity, light duration after
  motion, standby time, standby brightness and countdown.
- Keep the motion sensor switch (DP 56). "PIR may go" was read as the PIR state sensor;
  to be confirmed by the user.
- Rename "Battery probably low" to "Battery status" with the states Normal and Empty
  (on is Empty), as a binary sensor with translated states.
- Remove the retired entities from the entity registry at setup.
- Keep the DP schema and the write path in the code.

## D-014 More lamps and other users (2026-10-14)

**Context.** The user asked whether extra lamps can be added easily and whether other users
can pair their FlinQ Orion. The code binds to one lamp: the MAC address is hard-coded in
discovery, the mesh address defaults to 0x00B0, keys come only from a `tuya_ble_mesh` import
or manual entry, and every lamp needs its own entry with a hand-picked controller address.

**Considerations.** Home Assistant 2026.10 supports config subentries. The proxy filter
status that a lamp sends on every link has the lamp's own unicast address as source (176
in captures 14 and 17), so the address can be detected with only the NetKey. How other
users can get the keys is not known yet.

**Options.**
- Network entry with lamp subentries, plus key research. Gives: keys once, lamps added
  from a list, address detected, one controller identity per mesh. Costs: largest rebuild
  and a migration of the existing installation.
- Network entry with lamp subentries only. Costs: other users still cannot get keys.
- Clean-up only (no hard-coded MAC, detected address). Costs: every lamp still needs keys
  and a controller address by hand.
- Key research first. Costs: no code progress yet.

**Recommendation.** Network entry with lamp subentries, plus key research.

**Decision.** Network entry with lamp subentries, plus key research, chosen by Sylvan Loves.
Status: settled. Design details: D-015 after review of the plan.

## D-015 Design of the network and lamp model (2026-10-14)

**Context.** Implementation of D-014 in 0.4.0b1. Three questions were asked; the plan was
approved by Sylvan Loves.

| Detail | Options | Recommended | Decision |
|---|---|---|---|
| Finding new lamps | notification and manual, manual only | notification and manual | notification and manual |
| Links for several lamps | one link per lamp, one link with forwarding | one link per lamp | one link with forwarding |
| Plan | build, adjust first | build | build |

Design consequences, recorded for review (status open):

- One GATT link per network. A message for another lamp goes through the connected lamp
  as proxy. For lamps within radio range of each other this is the standard proxy function
  (GATT to advertising bearer and back); relay is needed only for more hops. Relay is not
  proven on these lamps.
- Fallback: when a lamp does not answer through the current link, the network connects to
  that lamp directly and repeats the request once. This was added during design.
- The controller identity (source address, sequence store, replay store) is per network and
  keeps its current store names, so the existing installation keeps address 2 and its
  sequence state.
- The mesh address of a lamp is taken from the source of the Proxy Filter Status when the
  lamp is the proxy.

**Decision.** As above, chosen by Sylvan Loves. Status: settled for the three questions;
open for the design consequences.

## D-016 Implementation choices in 0.4.0b1 (2026-10-14)

**Context.** While building D-014 and D-015, questions came up that the approved plan did not
answer. These choices were made during implementation and are recorded for review by the user.

**Considerations.** Mesh proxy service 0x1828 is advertised by every SIG Mesh proxy, of any
brand, so a lamp cannot be identified by the service alone. In HA 2026.10 a device belongs to
one subentry; an implicit move logs a deprecation warning and will fail from 2027.8.
`tuya_ble_mesh` stores the MAC address, the keys and the lamp address of the lamps it holds.
Measured once with the real HA 2026.10.1 registries (scratch script): when the device moved to
the subentry first, HA removed both entities that were still in the old subentry; with the
entities moved first, both kept their entity IDs.

**Options and choices.**

| Question | Options | Choice | Reason |
|---|---|---|---|
| Which Bluetooth discoveries to offer | every 0x1827/0x1828 device; only identified lamps | only identified lamps: Network ID of a configured network, or a `tuya_ble_mesh` entry with the same NetKey; with no network configured, a lamp held by `tuya_ble_mesh` | other choice notifies about devices of neighbours |
| A lamp of a configured network entered in "Add integration" | new network entry; add to the existing network | add to the existing network | one link and one controller identity per network |
| Two entries with the same NetKey | allow; refuse | refuse (error "network in use") | two controllers would compete for one network |
| Existing device and entities | let HA move them; move them in the migration | move the entities, then the device, in the migration | no deprecated implicit move; an implicit move would remove entities that are added later, such as the light |
| Mesh address of the migrated lamp | detect again; keep 176 | keep 176 when keys exist | same behaviour as 0.3; detection corrects a wrong value |
| Persistent mode, other lamps | no status read; read once through the link | read once through the link | otherwise the state of other lamps stays unknown until they report |
| Availability through another lamp's link | link means available; only for the lamp that holds it | only for the lamp that holds it | the link does not prove that another lamp is reachable |

**Recommendation.** As chosen above.

**Decision.** Made during implementation; status: open until reviewed by Sylvan Loves.

## D-017 How other users get the mesh keys (2026-10-14)

**Context.** Second part of D-014: other users must be able to pair their FlinQ Orion. Desk
research (docs/protocol.md, "How users get the mesh keys") showed that `tuya_ble_mesh` makes
its own keys and provisions the lamp after a factory reset, and that no open-source route reads
SIG Mesh keys from the Tuya cloud.

**Considerations.** Provisioning needs a factory reset, and the lamp then leaves Smart Life.
Configuration must finish within 30 s after provisioning (Tuya documentation). Reports on 2 to 4
other Tuya SIG Mesh devices show local control after No OOB provisioning; none is an Orion.
`tuya_ble_mesh` v0.38.1 has a bug in its AppKey Add call.

**Options.**
- Provisioning in this integration (0.5). Gives: pairing without other software. Costs: a reset,
  the lamp leaves Smart Life, a test needs a separate lamp.
- Keep import and manual entry. Costs: users depend on `tuya_ble_mesh` and its bug.
- Decide later. Costs: other users cannot pair for now.

**Recommendation.** Provisioning in this integration, tested on a separate lamp and not on the
working one.

**Decision.** Provisioning in this integration as 0.5, chosen by Sylvan Loves. Status: settled
for the direction; the design follows in a new plan.

## D-018 Motion sensor switch stays (2026-10-14)

**Context.** D-013 kept the motion sensor switch (DP 56) because "PIR may go" was read as the
PIR state sensor only, to be confirmed by the user.

**Options.** Keep the switch: motion detection can be turned on and off from Home Assistant,
one entity. Remove it: a smaller device page; the DP schema stays in the code.

**Recommendation.** Keep.

**Decision.** Keep, confirmed by Sylvan Loves. Status: settled; closes the open point of D-013.

## D-019 Small fixes after the first run of 0.4.0b1 (2026-10-14)

**Context.** The device diagnostics of the first 0.4.0b1 run (docs/protocol.md, "First run of
0.4.0b1") showed four points: a failed connection for a command leaves no record, the first
start-up attempt runs before a Bluetooth proxy is registered, the first read after a link setup
timed out at 2 s in both refreshes, and the `device_key` flag of sent messages is redacted
because it has the name of the key option.

**Options.**
- Now as 0.4.0b2. Gives: the next tests (second lamp, provisioning) also show failed
  connections. Costs: one more installation.
- With 0.5. Costs: the diagnostics gap stays during the provisioning tests.
- Only the two diagnostics fixes. Gives: no behaviour change based on one observation.

**Recommendation.** Now as 0.4.0b2.

**Decision.** Now as 0.4.0b2, chosen by Sylvan Loves. Status: settled.

## D-020 Release 1.0.0 (2026-10-14)

**Context.** The user asked for an official 1.0.0 release, a README for Home Assistant users
and a logo that Home Assistant shows for the integration.

**Considerations.** Home Assistant 2026.9.0 and later serve brand images of a custom
integration from its `brand` folder (checked in the 2026.9.0 and 2026.10.1 source), so no
pull request to the brands repository is needed. FlinQ is a trademark of another company and
this integration is not official, so the logo must be an own design. 0.4.0b2 had not run on
the lamp when 1.0.0 was chosen; only power control is confirmed physically, forwarding between
two lamps is not tested, and other users cannot pair without `tuya_ble_mesh` until
provisioning (D-017) exists.

| Question | Options | Recommended | Decision |
|---|---|---|---|
| Timing | after a test of 0.4.0b2; now; 0.4.0 now and 1.0.0 after provisioning | after a test of 0.4.0b2 | now |
| Logo | outdoor lamp with mesh nodes; lamp with sun; monogram | outdoor lamp with mesh nodes | outdoor lamp with mesh nodes, warm yellow and dark grey, transparent |
| CHANGELOG.md | add (Keep a Changelog); release notes only | add | release notes only |
| Release flow | all by Claude; pull request by Claude, merge by the user; later | all by Claude | pull request by Claude, merge by the user, then tag and release |

**Decision.** As in the Decision column, chosen by Sylvan Loves. Status: settled. Provisioning
(D-017) moves from 0.5 to a 1.x minor release.

## D-021 README icon, repository description and release commit (2026-10-14)

**Context.** After the merge of pull request #1 the user asked to remove the icon from the
README and to provide a description and topics for the repository.

| Question | Options | Recommended | Decision |
|---|---|---|---|
| Description and topics | set by Claude with `gh`; set by the user | set by Claude | set by Claude: an English description and 18 topics; `wip` removed |
| README change | push and pull request; keep local | push and pull request | push and pull request #2, merged by the user |
| Commit for tag v1.0.0 | after the README pull request; now on 79531b1; no release yet | after the README pull request | after the README pull request |

The integration icon in `custom_components/flinq_orion/brand/` stays (D-020).

**Decision.** As in the Decision column, chosen by Sylvan Loves. Status: settled.

## D-022 Design of pairing (provisioning) in 1.1.0 (2026-10-15)

**Context.** D-017 chose pairing in this integration. This entry records the design answers
of the planning round and the plan that the user approved.

**Considerations.**
- An Orion in pairing mode advertised 0x1827 service data with ASCII `key5wvvr` in bytes 8
  to 15 of its UUID (observed once, 2026-10-14). The cloud PID `1ajecbtl` is not in the UUID.
- Telink's default GATT layout renames the provisioning and proxy services in place after
  provisioning and does not disconnect; the Orion showed this layout.
- Tuya: configuration (AppKey, model bind, composition) must finish within 30 s after
  provisioning; the behaviour on timeout is not documented.
- An ESPHome proxy keeps stale GATT services unless `clear_cache()` runs while connected;
  connecting through it took 3 to 19 s in this installation.
- How the current lamp was paired is unknown, so pairing on the Orion is unproven.

| Question | Options | Recommended | Decision |
|---|---|---|---|
| How the current lamp was paired | `tuya_ble_mesh` after reset; Smart Life first; unknown | n/a | unknown |
| Lamp for the physical test | second lamp; reset the current lamp; offline only first | second lamp | second lamp |
| Reset procedure | power toggled 3 times; button; unknown | n/a | hold the mode button (duration to confirm) |
| Scope | new network and existing network; existing only; new only | both | both |
| Configuration after Complete | same link with fallback to reconnect; always reconnect | same link with fallback | same link with fallback |
| Configuration fails after pairing | keep the lamp and offer "Finish configuration"; discard | keep | keep |
| Devices that can be paired | Orion only; every Tuya SIG Mesh light | Orion only | Orion only |
| Publication Set to the controller | not now; include | not now | include |

**Recommendation.** As in the Recommended column; the plan is in the approved planning file
of 2026-10-15.

**Decision.** As in the Decision column, chosen by Sylvan Loves; plan approved. Status:
settled for the questions; the physical behaviour of the Orion is open until the test on the
second lamp.

## D-023 Implementation choices in 1.1.0 (2026-10-15)

**Context.** While building the approved pairing plan (D-022), some details came up that the
plan did not settle, and one detail differs from the plan. They are recorded for review.

| Question | Plan | Choice | Reason |
|---|---|---|---|
| First lamp address | lowest free block from 0x0002 | lowest free block from 0x0010 | room for controller addresses: the existing network already uses 1 (Tuya BLE Mesh) and 2 (this integration) |
| Name of the pairing store | `flinq_orion.{entry_id}.pairing` | `flinq_orion.pairing.{network fingerprint}` | a new network has no entry ID before its first lamp is paired |
| "Pair a new lamp" from Add integration when networks exist | not settled | choose a network or "New mesh network" | the same screen serves discovery with two or more networks |
| Order of model binds | light and vendor models first | light models, then the Tuya vendor model, then the other non-foundation models | the light works first if the lamp stops configuration early |
| Models that publish to the controller | 0x1000, 0x1300, 0x1303, 0x1307 | as planned; the Tuya vendor model does not publish | unknown what the vendor model publishes |
| Restart during the first pairing of a new network | not settled | the new keys are lost; the README says to reset and pair again | keys are stored only when the network entry is created |
| Result when a lamp is in pairing mode in a configured network | never offered | as planned, also in discovery | the working lamp entered pairing mode by itself on 2026-10-14 |

**Recommendation.** Keep these choices; change the first lamp address back to 0x0002 only if
the user prefers the plan.

**Decision.** Made during implementation; status: open until reviewed by Sylvan Loves.

## D-024 Beta for the first pairing test (2026-10-15)

**Context.** Pairing (D-022) is tested against sample data and simulated lamps, not on a real
Orion. D-023 left the first lamp address open for review.

| Question | Options | Recommended | Decision |
|---|---|---|---|
| Release of 1.1.0 | prerelease 1.1.0b1 for the test on the second lamp, then pull request and 1.1.0; pull request and 1.1.0 at once; nothing yet | prerelease first | prerelease 1.1.0b1 first |
| First lamp address (D-023) | from 0x0010; from 0x0002 as in the plan | from 0x0010 | from 0x0010 |

**Decision.** As in the Decision column, chosen by Sylvan Loves. Status: settled. This adds a
beta step to the release of D-022 and closes the address question of D-023.
