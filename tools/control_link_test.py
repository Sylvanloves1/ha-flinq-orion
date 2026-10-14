"""Read-only control link test from a second Bluetooth central, without mesh keys.

Connects to the lamp, subscribes to Mesh Proxy Data Out (0x2ADE) like Home Assistant does,
and reads the Device Information firmware string at a fixed interval as a liveness check.
It never writes to Mesh Proxy Data In or to any other characteristic. Run it only while
the FlinQ Orion integration is disabled, so that the ESPHome proxy does not compete.

Each output line is one JSON event with UTC time, for correlation with PacketLogger.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import time

from bleak import BleakClient, BleakScanner

PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
PROXY_OUT = "00002ade-0000-1000-8000-00805f9b34fb"
FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"


class EventLog:
    def __init__(self):
        self.opened: float | None = None

    def emit(self, event: str, **fields) -> None:
        link_s = None if self.opened is None else round(time.monotonic() - self.opened, 3)
        line = {"utc": datetime.now(timezone.utc).isoformat(), "link_s": link_s, "event": event}
        print(json.dumps({**line, **fields}), flush=True)


async def find_candidates(seconds: float) -> dict:
    found = await BleakScanner.discover(seconds, return_adv=True)
    return {
        address: (device, adv)
        for address, (device, adv) in found.items()
        if PROXY_SERVICE in adv.service_uuids or PROXY_SERVICE in adv.service_data
    }


async def run(args) -> None:
    log = EventLog()
    candidates = await find_candidates(args.scan_seconds)
    for address, (_, adv) in candidates.items():
        data = adv.service_data.get(PROXY_SERVICE, b"")
        log.emit(
            "candidate",
            device=address,
            rssi=adv.rssi,
            identity_type=data[0] if data else None,
            service_data=data.hex(),
        )
    if args.scan_only:
        return
    if args.device:
        if args.device not in candidates:
            log.emit("stop", reason="device_not_seen_advertising_0x1828")
            return
        device = candidates[args.device][0]
    elif len(candidates) == 1:
        device = next(iter(candidates.values()))[0]
    else:
        log.emit("stop", reason="choose_one_device_with_--device", candidates=len(candidates))
        return

    disconnected = asyncio.Event()

    def on_disconnect(_client):
        log.emit("disconnected")
        disconnected.set()

    def on_notify(_sender, data: bytearray):
        # Mesh proxy PDUs stay encrypted; only type and length are logged.
        log.emit("notification", proxy_type=data[0] & 0x3F if data else None, length=len(data))

    started = time.monotonic()
    log.emit("connecting", device=device.address)
    async with BleakClient(device, disconnected_callback=on_disconnect, timeout=20) as client:
        log.opened = time.monotonic()
        log.emit("connected", connect_s=round(log.opened - started, 3))
        if not args.no_notify:
            await client.start_notify(PROXY_OUT, on_notify)
            log.emit("subscribed", characteristic="0x2ADE")
        deadline = log.opened + args.seconds
        reads_ok = reads_failed = 0
        while time.monotonic() < deadline and not disconnected.is_set():
            asked = time.monotonic()
            try:
                value = await asyncio.wait_for(client.read_gatt_char(FIRMWARE_REVISION), 5)
                reads_ok += 1
                log.emit(
                    "read_ok",
                    latency_ms=round((time.monotonic() - asked) * 1000),
                    firmware=bytes(value).decode("utf-8", errors="replace"),
                )
            except Exception as err:  # every outcome is a measurement
                reads_failed += 1
                log.emit("read_failed", error=type(err).__name__)
            try:
                await asyncio.wait_for(disconnected.wait(), args.interval)
            except TimeoutError:
                pass
        log.emit(
            "summary",
            link_lifetime_s=round(time.monotonic() - log.opened, 3),
            disconnected_by_peer_or_timeout=disconnected.is_set(),
            reads_ok=reads_ok,
            reads_failed=reads_failed,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scan-seconds", type=float, default=15)
    parser.add_argument("--scan-only", action="store_true", help="list candidates and stop")
    parser.add_argument("--device", help="identifier printed by --scan-only")
    parser.add_argument("--seconds", type=float, default=180, help="how long to keep the link")
    parser.add_argument("--interval", type=float, default=10, help="seconds between reads")
    parser.add_argument("--no-notify", action="store_true", help="skip the 0x2ADE subscription")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
