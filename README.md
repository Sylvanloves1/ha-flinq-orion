# FlinQ Orion for Home Assistant

[![Release](https://img.shields.io/github/v/release/Sylvanloves1/ha-flinq-orion)](https://github.com/Sylvanloves1/ha-flinq-orion/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Tests](https://github.com/Sylvanloves1/ha-flinq-orion/actions/workflows/tests.yml/badge.svg)](https://github.com/Sylvanloves1/ha-flinq-orion/actions/workflows/tests.yml)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5)
[![License: MIT](https://img.shields.io/github/license/Sylvanloves1/ha-flinq-orion)](LICENSE)

Control the **FlinQ Smart Outdoor Lamp Orion** (FQC8274) locally from Home Assistant. The integration talks to the lamp over Bluetooth (Tuya SIG Mesh), through the Bluetooth adapter of Home Assistant or an ESPHome Bluetooth proxy. There is no cloud, no Smart Life app and no gateway in the command path.

> This is an independent community project. It is not made, supported or endorsed by FlinQ or Tuya.

## Quick install

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Sylvanloves1&repository=ha-flinq-orion&category=integration)

After the download and a restart:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=flinq_orion)

Then pair a lamp in pairing mode, or add a lamp with existing mesh keys. See [Setup](#setup).

## Features

- 💡 **Light**: on/off, brightness, colour and colour temperature.
- 🏃 **Motion sensor switch**: turn motion detection of the lamp on or off.
- 🔋 **Battery status**: shows "Empty" when the lamp goes into its low-power cycle.
- 📶 **Signal strength** of the lamp.
- 🔗 **Pairing**: pair a reset lamp directly from Home Assistant. No app, no account, no keys needed.
- 🕸️ **One mesh network, many lamps**: set the keys once, then add lamps from a list of nearby lamps.
- 🔌 **On-demand connections**: a short Bluetooth link per command, which saves the lamp battery.
- 🏠 **Local only**: no Internet connection or Tuya account is needed for daily use.
- 🩺 **Diagnostics**: per network and per lamp, with keys and addresses removed.

### What is confirmed

Tested with one Orion through an ESPHome Bluetooth proxy on Home Assistant 2026.10.1.

| Function | Status |
|---|---|
| On/off | Confirmed on a real lamp |
| Brightness, colour, colour temperature | The lamp reports these states; the physical result is not confirmed yet. Colour temperature uses 2000–6500 K, the range of the Home Assistant Tuya integration. |
| Motion sensor switch | Built from the Tuya data point schema; the physical result is not confirmed yet |
| Battery status | Derived from the advertising pattern of the lamp; the lamp has no battery data point |
| Several lamps in one network | Two lamps in one network work (user report, 2026-10-16); whether commands went through one link is not recorded yet |
| Pairing | Paired once on a real Orion (user report, 2026-10-16); also tested against the official Bluetooth Mesh sample data and with simulated lamps |

Please report your results in an [issue](https://github.com/Sylvanloves1/ha-flinq-orion/issues), with the diagnostics attached.

## Requirements

- **Home Assistant 2026.9 or newer.**
- **Bluetooth near the lamp**: the Home Assistant Bluetooth integration with a local adapter, or an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) with active connections enabled.
- **One of these:**
  - **A lamp you can reset.** The integration pairs it and creates the mesh network with new keys. The lamp then leaves Smart Life.
  - **The mesh keys of an existing network**: NetKey, AppKey and the IV Index, imported from the [Tuya BLE Mesh](https://github.com/11z4t/tuya-ble-mesh) integration or entered by hand, plus a free controller address. The integration does not read keys from the Smart Life app or the Tuya cloud; no public way to get them from Tuya is known.

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Open the menu (three dots, top right) and select **Custom repositories**.
3. Enter `https://github.com/Sylvanloves1/ha-flinq-orion`, select the type **Integration** and select **Add**.
4. Search for **FlinQ Orion** and select **Download**.
5. Restart Home Assistant.

The "Quick install" button above does steps 1 to 3 for you.

### Manual

1. Download the latest [release](https://github.com/Sylvanloves1/ha-flinq-orion/releases).
2. Copy the folder `custom_components/flinq_orion` to `/config/custom_components/flinq_orion`.
3. Restart Home Assistant.

## Setup

### Pair a lamp (no keys needed)

1. Put the lamp in pairing mode: hold its mode button until the lamp blinks. Pairing mode lasts up to 6 minutes.
2. Go to **Settings → Devices & services → Add integration → FlinQ Orion → Pair a new lamp**. Home Assistant can also offer the lamp itself as a discovered device.
3. Select the lamp, give it a name and wait up to a minute. Keep the lamp close to the Bluetooth proxy.

The integration creates a new mesh network with random keys, or uses one of your networks. It pairs the lamp, adds the AppKey, binds the light models and the Tuya model, and asks the lamp to send its state changes to the controller address of Home Assistant. Home Assistant receives them while a Bluetooth link is open.

To pair more lamps, open the network and select **Add lamp → Pair a new lamp**.

If the lamp joined the network but its configuration did not finish, Home Assistant shows a repair notice. Open the lamp, select **Reconfigure → Finish configuration**. If Home Assistant restarted during pairing, use **Add lamp → Restore an interrupted pairing**.

A lamp that is already in one of your networks is never offered for pairing, also when it blinks after a power cycle: it returns to its network after the pairing window.

### Add a lamp with existing mesh keys

#### 1. Add the mesh network and the first lamp

Go to **Settings → Devices & services → Add integration → FlinQ Orion → Add a lamp with existing mesh keys**. Enter the Bluetooth address and a name of your first lamp. This creates the mesh network with the lamp in it.

Home Assistant can also find the lamp itself. It offers a lamp only when the lamp belongs to a network that you already configured, or when the Tuya BLE Mesh integration holds the lamp. Mesh devices of your neighbours are ignored.

Without keys the integration stays in observation mode: it shows the signal strength and collects diagnostics, but it does not send anything.

#### 2. Enter the keys

Before you activate the network:

- Disable (do not delete) the Tuya BLE Mesh entry of the lamp, so that two integrations do not connect at the same time.
- Close the Smart Life app.

Open the network and select **Configure**:

1. Optional: under **Import existing mesh credentials from Tuya BLE Mesh**, select the entry of your lamp and submit. The keys and the IV Index are filled in. The mesh address and the DeviceKey are stored with that lamp.
2. Select the **Protocol mode**. Use `sig` for the Orion.
3. Keep the **Connection mode** at `on_demand`.
4. Enter a **dedicated unused controller address** and confirm that it was never used. Do not reuse the address of the Tuya BLE Mesh integration; the form blocks it.
5. Submit the form and leave the light metadata settings off unless you have confirmed them.

| Protocol mode | What it does |
|---|---|
| `observe` | Passive diagnostics only, nothing is sent |
| `query_only` | Reads the state, does not control the light |
| `sig` | Standard SIG Mesh light control (recommended for the Orion) |
| `vendor_dp` | Experimental control through Tuya data points |

#### 3. Add more lamps

Open the network and select **Add lamp → Add a lamp that is already in this network**. The list shows nearby lamps. Lamps that advertise the Network ID of your keys come first, marked "this network". A lamp with a low battery is marked "network not verified". You can also type the Bluetooth address.

The mesh address of a lamp is found on its first connection. To rename a lamp, or to change its mesh address or DeviceKey, select **Reconfigure** on the lamp.

## Entities

Each lamp is a device with these entities:

| Entity | Type | Notes |
|---|---|---|
| Light (the lamp name) | light | Appears after the lamp reported its power state |
| Motion sensor | switch | Turns motion detection on or off |
| Battery status | binary sensor | Normal or Empty; diagnostic |
| Signal strength | sensor | dBm; diagnostic |
| Query status and capabilities | button | Reads the lamp state; diagnostic |

## Connection modes

| Mode | Behaviour |
|---|---|
| `on_demand` (default) | A command opens a link. The link closes after 60 s without activity. The state is read at start-up and every 30 minutes while the lamp is seen. The light stays available even without recent Bluetooth advertisements. |
| `persistent` | One link stays open and reconnects after a loss. The light stays available when the link is down; commands reconnect before sending. |

The first command after a pause takes 1–4 s, because the link must be set up. Commands within 60 s use the open link. If connecting or sending fails, the action reports an error and the light remains available for another attempt. State changes are shown only after a report from the lamp.

One link serves the whole network. A command for another lamp goes through the lamp that holds the link. If that lamp does not answer, the integration connects to the other lamp directly and sends the command once more.

## The lamp runs on a battery

The Orion is a solar lamp with a Li-ion battery. With a low battery it wakes for a few seconds about every 3 minutes, and no Bluetooth link works. The **Battery status** entity then shows "Empty". Charge the lamp over USB-C when it gets little sun.

Do not use pairing mode or switch the power on and off quickly to fix a connection. Three quick power toggles reset Tuya lamps, and a reset lamp leaves your mesh network.

## Actions

The actions select a lamp **device**.

| Action | What it does |
|---|---|
| `flinq_orion.query_status` | Reads the lamp state; returns the result |
| `flinq_orion.query_dp` | Reads one Tuya data point |
| `flinq_orion.dump_capabilities` | Returns the models and data points that the lamp reported; does not contact the lamp |
| `flinq_orion.probe_link` | Measures how long Bluetooth links last, with read-only requests |
| `flinq_orion.capture` | Records Bluetooth traffic for diagnostics |
| `flinq_orion.bind_vendor_model` | Advanced: binds an existing AppKey to the Tuya vendor model; needs the DeviceKey and explicit confirmation |

Example:

```yaml
action: flinq_orion.query_status
data:
  device_id: <select your lamp in the action editor>
response_variable: status
```

## Troubleshooting

| Symptom | What to do |
|---|---|
| "Lamp not reachable over Bluetooth" | Check the Battery status and charge the lamp. Check that a Bluetooth proxy is near the lamp and has a free connection slot. |
| A light command fails | The light stays available so you can retry. Check the battery, Bluetooth range and free proxy connection slots. |
| The first command is slow | Normal in `on_demand` mode: the link is set up first (1–4 s). |
| No light entity | The light appears after the lamp reported its power state. Select **Query status and capabilities**. |
| Changes made on the lamp do not show | The lamp does not report its own changes, such as motion switching. |

To collect information for an issue:

1. Turn on debug logging:

   ```yaml
   action: logger.set_level
   data:
     custom_components.flinq_orion: debug
   ```

2. Reproduce the problem.
3. Download the diagnostics: from the network entry (link history and all lamps) or from the lamp device. Keys and Bluetooth addresses are removed.

Never share your keys, a full backup or an unfiltered Bluetooth capture.

## Limitations and roadmap

- **Pairing worked once on a real Orion.** Which functions of the paired lamp work physically is not recorded yet. A paired lamp leaves Smart Life.
- Removing a lamp from Home Assistant does not remove it from the mesh network. Reset the lamp if you want to use it elsewhere.
- If Home Assistant restarts during the first pairing of a new network, the new keys are lost: reset the lamp and pair it again.
- The lamp does not send its own changes to Home Assistant.
- Whether commands for one lamp go through the link of another physical lamp is not recorded yet.
- Colour and colour temperature mapping are not physically confirmed.
- A change of the IV Index or a key refresh in the mesh network stops sending until you repair the configuration. There is no automatic recovery.

## Security and privacy

- Keys are stored in the Home Assistant configuration, without extra encryption. Protect your backups.
- Diagnostics remove keys, Bluetooth addresses and other identifiers.
- The integration keeps a sequence counter per controller address. Do not delete its storage files or restore an old copy of them. After a restore of an old backup, choose a new, unused controller address.
- There is no reset action and no way to send arbitrary packets.

## Technical documentation

- [Technical notes](docs/technical.md): protocol modes, key import details and the first hardware test
- [Protocol findings](docs/protocol.md): what is known about the lamp, with captures
- [Decision log](docs/decision-log.md): why the integration works the way it does
- [Test status](docs/testing.md) and test procedures ([pairing](docs/pairing-test.md), [network](docs/network-test.md), [on demand](docs/on-demand-test.md), [link](docs/link-test-procedure.md))
- [Development](docs/development.md)

## Contributing

Issues and pull requests are welcome. Attach the diagnostics to an issue. To run the checks locally:

```bash
pip install -r requirements-dev.txt
ruff check custom_components tests
ruff format --check custom_components tests
mypy custom_components/flinq_orion
pytest -q
```

## License

[MIT](LICENSE). FlinQ, Orion, Tuya and Smart Life are trademarks of their owners. They are used here only to identify the supported device.
