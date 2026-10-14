# Opt-in link timeout experiment — 0.2.8b1

This prerelease is not a validated fix. Normal connections behave as in 0.2.7.
The action `flinq_orion.test_connection_parameters` applies one request to the
current/next connection, keeping 40 ms and latency 0 and requesting 8 s supervision
timeout. On a new connection it waits 1.25 s after discovery to let the observed
second parameter update occur. This timing is capture-derived, not a protocol
requirement. No preference, keys, addresses or sequence stores are changed.

Install the prerelease explicitly in HACS (enable beta versions / redownload),
restart HA, and keep the proxy's esp32_ble VERBOSE logging enabled. Open live
proxy logs. Power-cycle the lamp once, then execute the action below immediately
when RSSI updates; do not press the normal query button for this experiment.

```yaml
action: flinq_orion.test_connection_parameters
data:
  entry_id: 01M4WS80AEVFFKNPA4DWWVWYNK
  confirm: true
```

Wait 60 seconds and test on/off twice. Save the proxy log and download FlinQ
diagnostics whether the test succeeds or fails. Verify a GAP update reports
interval=32, latency=0, timeout=800. `api_returned` in diagnostics proves only
API completion, not accepted settings. A connection that merely fails later is
not a successful result. Subsequent reconnects use ordinary settings unless
the action is invoked again. Reinstall 0.2.7 to remove the experimental action.
