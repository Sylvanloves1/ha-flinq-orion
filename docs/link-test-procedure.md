# Link test procedure for 0.2.9b1

This prerelease is a test build, not a validated fix. Use it for one controlled test.
The test answers two questions:

1. Does the lamp still advertise our mesh network and unicast address 0x00B0?
2. Does a cached ESPHome proxy connection keep the link, when uncached connections do not?

Background and evidence: [protocol.md](protocol.md), section "Source audit and capture
correlation (0.2.9b1)", and [decision-log.md](decision-log.md), D-001.

## Safety rules

- Do not power-cycle the lamp unless step 4 tells you to. Tuya documents three on/off
  toggles as the reset gesture for lighting products. The lamp showed a provisioning
  GATT table and an Unprovisioned Device beacon once (2026-10-14 12:15 UTC).
- If you must power-cycle: do it one time. Keep the lamp off for at least 30 s. Then keep
  it on for at least 2 minutes before you do anything else.
- Do not open Smart Life. Do not add the lamp to Smart Life again.
- Do not delete or reset the FlinQ Orion config entry, the sequence store or the replay
  store. Do not change the controller source address.
- Do not share diagnostics files that you did not download through Home Assistant. They are
  redacted, but check them before you share them.

## Preparation

1. Keep the ESPHome proxy logging as it is now: `esp32_ble` at VERBOSE. The proxy log must
   show the lines `Connecting v3 with cache`, `Connecting v3 without cache`,
   `Connection open`, `Conn params updated` and `DISCONNECT_EVT`.
2. Install 0.2.9b1 and restart Home Assistant. A restart clears the GATT service cache
   that Home Assistant holds for the proxy. The first connection after the restart is
   therefore always uncached.
3. Set the log levels in Developer tools, Actions:

   ```yaml
   action: logger.set_level
   data:
     custom_components.flinq_orion: debug
     bleak_esphome: debug
   ```

   `bleak_esphome` writes `Cached services hit` or `Cached services miss` for each
   connection. This gives a second source for the connection type.
4. Start the live proxy log and keep it running until the end of step 5.

## Step 1: passive network check (5 minutes, no connection needed)

1. Wait 5 minutes after the restart. Do not touch the lamp.
2. Download the FlinQ Orion diagnostics (file 1).
3. Read `data.state.network_evidence` and `data.state.network_evidence_counts`:

| Result | Meaning | Next action |
|---|---|---|
| `network_id_match` | The lamp advertises our Network ID. | Continue with step 2. |
| `node_identity_match` | The lamp advertises Node Identity for our network and address 0x00B0. | Continue with step 2. |
| `mismatch` | The advertised identity does not match our NetKey or address. | Stop. Do not power-cycle. Report the result. |
| `unprovisioned_packet: true` | The latest packet contains Unprovisioned Device data (0x1827). | Stop. Do not power-cycle. Report the result. |
| `no_proxy_identity` | No proxy identity received yet. | Wait 5 more minutes and download again. |

A match proves that the lamp holds our NetKey and address 0x00B0. It does not prove that
the AppKey binding is still present.

## Step 2: start the probe

Run this action as soon as step 1 gives a match:

```yaml
action: flinq_orion.probe_link
data:
  entry_id: 01M4WS80AEVFFKNPA4DWWVWYNK
  seconds: 600
  interval: 10
```

The probe sends one read-only Generic OnOff Get every 10 seconds for 10 minutes. When the
link is lost, the next read connects again through the normal path. The probe does not
change the light. Do not operate the light, the switch or Smart Life during the probe.

## Step 3: watch the connection type

Note the line before each `Connection open` in the proxy log:

- `Connecting v3 without cache`: expected for the first connection after the restart.
- `Connecting v3 with cache`: the path that 0.2.7 and 0.2.8b1 never used.

## Step 4: only if the lamp does not connect

If the proxy log shows no `Connection open` for 10 minutes, power-cycle the lamp one time
as described in the safety rules. Do not restart Home Assistant. A restart clears the
cache again.

## Step 5: collect the results

When the action returns:

1. Copy the response of the action (the summary).
2. Download the FlinQ Orion diagnostics (file 2).
3. Save the complete proxy log of the test.
4. Save the Home Assistant log lines that contain `Cached services`.

## How to read the result

For each link, use `data.connection_history` in file 2. A `disconnected` record gives
`link_lifetime_s` and the setup stage. `link_probe` records show which reads received a
response and on which link (`generation`).

| Observation | Conclusion |
|---|---|
| A cached link carries 12 or more consecutive probe responses (120 s or more), and uncached links stop within 21 s | Supports H2: the uncached proxy path is the problem. |
| Cached and uncached links both stop within 21 s | H2 is rejected. H1 (lamp state) remains. The next decision is about the lamp, not about code. |
| Cached and uncached links both stay up | The lamp state changed. Neither hypothesis is confirmed. |
| No cached link occurs | No conclusion. Report the proxy log. |

For each cached link, also note whether the proxy log shows
`Conn params updated: interval=32 ... timeout=200` and the time from `Connection open`
to that line.

One test is one observation. A positive result is "observed once", not a proven fix.
Do not promote 0.2.9b1 to a stable release on the basis of this test alone.
