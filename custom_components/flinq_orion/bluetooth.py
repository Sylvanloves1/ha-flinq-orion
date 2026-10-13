"""HA-selected GATT backend, including ESPHome remote connections."""

import asyncio
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from .const import PROXY_SERVICE, PROXY_OUT
from .protocol.proxy import ProxySAR
from .protocol.errors import DeviceUnavailableError


class Inspector:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.lock = asyncio.Lock()
        self.client = None
        self.task = None
        self.stopping = False

    async def capture(self, seconds=20):
        async with self.lock:
            if self.stopping:
                raise DeviceUnavailableError("Integration is unloading")
            c = self.coordinator
            device = bluetooth.async_ble_device_from_address(c.hass, c.address, connectable=True)
            if device is None:
                raise DeviceUnavailableError(
                    "No connectable HA Bluetooth source can reach the lamp"
                )
            sar = ProxySAR()

            def notification(handle, data):
                try:
                    result = sar.feed(bytes(data))
                    if result:
                        kind, payload = result
                        if kind == 0:
                            c.network_received(payload)
                        else:
                            c.record({"kind": "proxy", "type": kind, "payload": payload.hex()})
                except Exception as err:
                    c.record({"kind": "proxy_error", "error": type(err).__name__})

            client = await establish_connection(BleakClientWithServiceCache, device, "FlinQ Orion")
            if self.stopping:
                await client.disconnect()
                raise DeviceUnavailableError("Integration unloaded while connecting")
            self.client = client
            self.task = asyncio.current_task()
            try:
                c.data["gatt"] = [
                    {
                        "uuid": service.uuid,
                        "characteristics": [
                            {"uuid": char.uuid, "properties": char.properties}
                            for char in service.characteristics
                        ],
                    }
                    for service in client.services
                ]
                c.record({"kind": "gatt", "services": c.data["gatt"]})
                if client.services.get_service(
                    PROXY_SERVICE
                ) and client.services.get_characteristic(PROXY_OUT):
                    await client.start_notify(PROXY_OUT, notification)
                    c.record(
                        {
                            "kind": "capture",
                            "status": "proxy_notifications_started",
                            "seconds": seconds,
                        }
                    )
                    await asyncio.sleep(seconds)
                    c.record({"kind": "capture", "status": "complete"})
                else:
                    c.record({"kind": "capture", "status": "no_mesh_proxy_service"})
                c.async_set_updated_data(dict(c.data))
            finally:
                try:
                    await client.disconnect()
                finally:
                    self.client = None
                    self.task = None

    async def stop(self):
        self.stopping = True
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
