# Test procedure for 0.3.0b1 (on-demand connections)

This prerelease is a test build. One successful run is one observation, not a validated
release.

## Preconditions

- The lamp is charged (USB-C or sun) and switched on. Do not use pairing mode.
- Only the FlinQ Orion integration controls the lamp. The Bluetooth Mesh custom
  integration stays disabled. The built-in Bluetooth and ESPHome integrations stay enabled.
- Proxy logging as before (`esp32_ble` at VERBOSE) helps, but is not required.

## Steps

1. Install 0.3.0b1 and restart Home Assistant.
2. Open the FlinQ Orion options. Check that **Connection mode** is `on_demand` (the
   default). Save without other changes.
3. Wait 2 minutes. Download the diagnostics (file 1). `connection_history` must contain a
   `refresh` record, followed by an `idle_disconnect` about 60 s later.
4. Wait until at least 2 minutes have passed since the `idle_disconnect`. Turn the light on
   in Home Assistant. Note the time until the state changes in the dashboard.
5. Within 30 s, turn the light off. Note the time again.
6. Wait 90 s. Download the diagnostics (file 2).

In daylight the light can stay dark because of the lamp's `auto` mode. The state change
in Home Assistant is the measurement, not the light output.

## How to read the result

| Observation | Meaning |
|---|---|
| Step 4 takes 1–5 s, step 5 under about 1 s | Expected: a new link for the first command, the open link for the second. |
| `idle_disconnect` after each period of use, no `disconnected` records | Links close as designed; no link was lost. |
| `disconnected` records with a short `link_lifetime_s` | Links are lost. Check the lamp battery first. |
| Error "Lamp not reachable over Bluetooth" | No link within about 20 s. Check the battery and whether the lamp advertises. |

Send both diagnostics files and the two times you noted.

## Settings and reports (same installation)

7. After step 3, open the device page. All settings (light mode, control mode, light sensor
   threshold, motion sensitivity, light duration after motion, motion sensor, standby time,
   standby brightness, countdown, PIR state) must show a value, not "unknown".
   "Unknown" for all of them means that DP reports are still not received; send file 1.
8. Compare the values with the last cloud status (2026-10-06): control mode automatic,
   threshold 2000 lux, sensitivity low, light duration 5 s, motion sensor off, standby
   time 1 min, standby brightness 0 %. A difference is possible if the lamp was changed later.
9. Write test: set "Light duration after motion" from its value to 10 s. The entity must
   show 10 s within a few seconds. Then set it back. If the entity shows an error or keeps
   the old value, send the diagnostics.
10. "Battery probably low" must be off while the lamp is charged.
