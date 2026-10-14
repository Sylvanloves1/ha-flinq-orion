# Control link test with a Mac and PacketLogger

This test connects to the lamp from a Mac instead of the ESPHome proxy. It answers one
question: does the lamp lose the link with a different Bluetooth central too?

- If the Mac also loses the link about 2 s after the lamp's parameter update, the cause is
  in the lamp.
- If the Mac keeps the link, the cause is in the combination of this lamp and the ESP32
  proxy.

No mesh keys are used. The script never writes to Mesh Proxy Data In or to any other
characteristic. It only enables notifications on 0x2ADE, as Home Assistant does, and
reads the firmware string as a liveness check.

Background: [protocol.md](protocol.md), section "Test result 0.2.9b1"; decision D-006 in
[decision-log.md](decision-log.md).

## Safety rules

The rules of [link-test-procedure.md](link-test-procedure.md) apply. In short: do not
power-cycle the lamp, unless step 3 tells you to. Do not open Smart Life.

## Privacy

A PacketLogger trace contains all Bluetooth traffic of the Mac. That includes keyboards,
mice and headphones. Do not share the complete trace. Share only the entries listed in
step 6. Turn off other Bluetooth accessories during the test if you can.

## Preparation

1. Download "Additional Tools for Xcode" from https://developer.apple.com/download/all
   (a free Apple account is enough). PacketLogger is in the `Hardware` folder.
2. Install the profile "Bluetooth for macOS" from
   https://developer.apple.com/bug-reporting/profiles-and-logs/?platform=macos and follow
   Apple's `Bluetooth_Logging_Instructions.pdf` on that page. The PDF needs a login and was
   not checked for this document. Follow it as Apple writes it.
3. Put the Mac as close to the lamp as you can.
4. The first time the script runs, macOS asks for Bluetooth access for Terminal. Allow it.

## Steps

1. In Home Assistant, disable the FlinQ Orion integration: Settings, Devices & services,
   FlinQ Orion, menu, Disable. Disabling stops all connection attempts. It does not
   delete keys, the sequence store or the replay store. Wait 1 minute.
2. Start PacketLogger and start a capture of the Mac. Check that live entries appear.
   If PacketLogger shows no entries at all, continue anyway. The script output alone
   answers the main question.
3. List the candidates:

   ```sh
   cd ~/personal-projects/ha-flinq-orion
   .venv/bin/python tools/control_link_test.py --scan-only
   ```

   Expect one `candidate` with `identity_type` 1 or 0. Note its `device` value. If there is
   no candidate for 10 minutes, power-cycle the lamp one time (off for at least 30 s), wait
   2 minutes and scan again.
4. Run the test for 3 minutes:

   ```sh
   .venv/bin/python tools/control_link_test.py --device <device> --seconds 180 | tee control-link.jsonl
   ```

5. Stop the PacketLogger capture and save it locally. Enable the FlinQ Orion integration
   again.
6. In PacketLogger, find these entries for the lamp connection and copy them as text or
   screenshots:
   - LE Connection Complete (connection interval, supervision timeout)
   - L2CAP LE Signalling: Connection Parameter Update Request and Response
   - LE Connection Update Complete (new interval, latency, timeout)
   - Disconnection Complete (reason), if present

Send `control-link.jsonl` and the entries of step 6.

## How to read the result

| Observation | Conclusion |
|---|---|
| Link kept for 180 s, reads OK, and PacketLogger shows the update to 20–40 ms / 2 s | The lamp handles its own update with a Mac. The cause is in the lamp and ESP32 combination. |
| Link kept for 180 s, no update request seen | The lamp keeps a link at the Mac's interval. The update was not tested. The ESP32 path stays the suspect. |
| `disconnected` about 2 s after an LE Connection Update Complete | Same pattern as with the ESP32. The cause is in the lamp. |
| `disconnected` without an update | The lamp or the radio link fails. Report the data. |
| All reads fail from the start, but no `disconnected` | macOS may block the Device Information service. The `disconnected` event is still the link signal. |

One test is one observation. Report it as such.
