# Test procedure for 0.4.0b1 (networks and lamps)

This prerelease is a test build. One successful run is one observation, not a validated
result. Keep the lamp charged during the test (see the README, "The Orion runs on a battery").

## Step 1: upgrade

1. Install 0.4.0b1 and restart Home Assistant.
2. Open Settings, Devices & services, FlinQ Orion. Expected: one entry (the network) with one
   lamp under it. The lamp has the same name, entities and history as before.
3. Open the Options of the network. Expected: keys, controller address 2 and the connection
   mode as before. The fields "DeviceKey" and "Primary lamp address" are no longer there.
4. Open **Reconfigure** on the lamp. Expected: mesh address 176 and the DeviceKey if one was
   set before.
5. Check Settings, System, Logs for warnings from `flinq_orion` or the device registry.

## Step 2: control

1. Turn the light on and off. Expected: same behaviour as 0.3.0b2.
2. Download the network diagnostics. Expected: `data.network.connection_history` contains a
   `connection_ready` record with `proxy_node` 176.

## Step 3: a second lamp (only when one is available)

1. Charge the second lamp. It must be in the same mesh network (same keys).
2. Open the network entry and select **Add lamp**. Expected: the lamp is in the list as
   "this network". Select it and give it a name.
3. Turn the second lamp on and off. Download the diagnostics of both lamps.
4. Report: did the command go through the first lamp's link (one `link_open` in
   `data.network.connection_history`) or did a `relay_fallback` record appear on the second
   lamp? Did the second lamp get its mesh address (`node_address` record)?

Do not reset a lamp to make this test work. A lamp that is not in this mesh network cannot be
added with this version, because the integration does not provision lamps.
