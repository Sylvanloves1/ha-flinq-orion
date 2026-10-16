# Test procedure for 1.1.0 (pairing)

Pairing is tested against the official Bluetooth Mesh sample data and simulated lamps only.
This procedure is the first test on a real Orion. One successful run is one observation.

Use the **second lamp**. Do not reset the working lamp (D-022).

## Before the test

1. Install 1.1.0 and restart Home Assistant.
2. Charge the second lamp over USB-C.
3. Place the second lamp within about 2 m of the Bluetooth proxy.
4. Turn on debug logging:

   ```yaml
   action: logger.set_level
   data:
     custom_components.flinq_orion: debug
   ```

## Step 1: pairing mode

1. Hold the mode button of the second lamp until it blinks. Write down how long you held it.
2. Within one minute, open the network and select **Add lamp → Pair a new lamp**.
3. Expected: the lamp is in the list with its Bluetooth address. If the list is empty, the lamp
   does not advertise the Orion product key `key5wvvr`; download the network diagnostics and
   stop here.

## Step 2: pair into the existing network

1. Select the lamp, give it a name and submit. Wait for the progress screen to finish.
2. Expected: the lamp appears under the network. No repair notice "Finish the configuration".
3. Download the network diagnostics (file 1). In `data.network.packets`, look for `pairing`
   records (`connected`, `capabilities`, `complete`) and the `configuration` record with its
   `results`.

If the progress ends with an error, download the diagnostics and note the error text. Put the
lamp in pairing mode again before a new attempt.

## Step 3: control

1. Turn the second lamp on and off, change the brightness, the colour temperature and the
   colour. Note what the lamp does physically.
2. Turn the motion sensor switch off and on.
3. Turn the first lamp on and off as well.
4. Download the network diagnostics (file 2). Report: did commands for the second lamp go
   through the link of the first lamp, or did a `relay_fallback` record appear?

## Step 4: published state (optional)

1. While a link is open (within 60 s after a command), switch the second lamp with its own
   button or let the motion sensor switch it.
2. Download the network diagnostics (file 3). In `data.network.packets`, look for `access`
   records from the address of the second lamp that are not answers to a command.

## Later: a new network

To test pairing into a new network, reset the second lamp again (mode button), then use
**Add integration → FlinQ Orion → Pair a new lamp → New mesh network**. Remove the lamp from
the first network in Home Assistant first.

Send files 1 to 3 and the notes. Never send the keys, a full backup or a Bluetooth capture.
