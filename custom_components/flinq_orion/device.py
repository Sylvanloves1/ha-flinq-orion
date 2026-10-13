"""Device-level confirmed reports and experimental command selection."""

from dataclasses import asdict
from collections.abc import Callable, Awaitable
from typing import Any
import asyncio
import time
from .protocol.errors import MeshError, MeshTimeoutError, TuyaDPError
from .protocol.vendor_tuya import DP, DATA, COMPANY_ID, decode_report, query, write
from .protocol.models import decode_composition
from .protocol.conversions import decode_hsv_string, encode_hsv_string


class OrionDevice:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.state: dict = {}
        self.history: dict[str, list[dict]] = {}
        self.composition: dict | None = None
        self.controller = None
        self.ensure_connection = None
        self.options = coordinator.entry.options
        self.tid = 0
        self.operation_lock = asyncio.Lock()
        self.tid_lock = asyncio.Lock()
        self.tid_last = [time.monotonic()] * 256
        self.report_keys: set[str] = set()

    @property
    def mode(self):
        return self.options.get("transport_mode", "observe")

    def dp(self, identifier):
        return self.coordinator.capabilities.known_dps.get(str(identifier))

    def has_dp(self, identifier, kind):
        value = self.dp(identifier)
        return value is not None and value["type"] == kind

    @property
    def supports_power(self):
        return (self.mode == "vendor_dp" and self.has_dp(1, 1)) or (
            self.mode == "sig" and "power" in self.state
        )

    @property
    def supports_brightness(self):
        return (self.mode == "vendor_dp" and self.has_dp(3, 2)) or (
            self.mode == "sig" and "brightness" in self.state
        )

    @property
    def temperature_range(self):
        if self.mode == "sig":
            return self.state.get("temperature_range")
        lo, hi = self.options.get("min_kelvin", 0), self.options.get("max_kelvin", 0)
        return (
            (lo, hi) if self.options.get("ct_range_confirmed") and 800 <= lo < hi <= 20000 else None
        )

    @property
    def supports_temperature(self):
        return (
            bool(self.temperature_range)
            and self.supports_brightness
            and (
                (self.mode == "vendor_dp" and self.has_dp(4, 2))
                or (self.mode == "sig" and "kelvin" in self.state)
            )
        )

    @property
    def supports_color(self):
        if self.mode == "sig":
            return "hsl" in self.state
        dp = self.dp(5)
        if not (self.options.get("hsv_encoding_confirmed") and dp and dp["type"] == 3):
            return False
        try:
            decode_hsv_string(dp["value"])
            return True
        except ValueError:
            return False

    @property
    def scenes(self):
        if not self.options.get("scene_replay_confirmed") or self.mode != "vendor_dp":
            return {}
        return {
            f"Scene {i + 1}": item
            for i, item in enumerate(self.history.get("6", []))
            if item["type"] in (0, 3)
        }

    def persist(self):
        return {"state": self.state, "history": self.history, "composition": self.composition}

    def restore(self, data):
        self.state = data.get("state", {})
        self.history = {
            k: v[-32:] for k, v in data.get("history", {}).items() if isinstance(v, list)
        }
        self.composition = data.get("composition")

    async def response(self, response):
        access = response.access
        if access.company == COMPANY_ID and access.opcode == DATA and not response.device_key:
            dps = decode_report(access.payload)
            for dp in dps:
                self.coordinator.capabilities.observe(dp)
                self.coordinator.record({"kind": "dp", "dp": dp.id, **dp.diagnostics()})
                key = str(dp.id)
                self.report_keys.add(key)
                if dp.id == 2 and dp.type == 4:
                    if dp.value == self.options.get("color_mode_value", -1):
                        self.state["last_color_mode"] = "hs"
                    elif dp.value == self.options.get("white_mode_value", -1):
                        self.state["last_color_mode"] = "temperature"

                values = self.history.setdefault(key, [])
                encoded = dp.diagnostics()
                if encoded not in values and len(values) < 32:
                    values.append(encoded)
        elif response.device_key and access.company is None and access.opcode == 2:
            composition = decode_composition(access.payload)
            self.composition = asdict(composition)
            self.coordinator.capabilities.sig_models = sorted(
                {model for element in composition.elements for model in element.sig_models}
            )
            self.coordinator.capabilities.vendor_models = sorted(
                {model for element in composition.elements for model in element.vendor_models}
            )
        elif not response.device_key and access.company is None:
            self.decode_sig(access.opcode, access.payload)
            self.report_keys.add(f"sig_{access.opcode:04x}")
        self.coordinator.report_received()

    def decode_sig(self, opcode, payload):
        if opcode == 0x8204 and len(payload) in (1, 3) and payload[0] in (0, 1):
            self.state["power"] = bool(payload[0])
        elif opcode == 0x824E and len(payload) in (2, 5):
            self.state["brightness"] = int.from_bytes(payload[:2], "little")
        elif opcode == 0x8278 and len(payload) in (6, 7):
            self.state["hsl"] = tuple(
                int.from_bytes(payload[i : i + 2], "little") for i in (0, 2, 4)
            )
        elif opcode == 0x8263 and len(payload) == 5 and payload[0] == 0:
            lo, hi = int.from_bytes(payload[1:3], "little"), int.from_bytes(payload[3:5], "little")
            if 800 <= lo < hi <= 20000:
                self.state["temperature_range"] = (lo, hi)
        elif opcode == 0x8263 and payload:
            self.coordinator.record({"kind": "sig_range_error", "status": payload[0]})
        elif opcode == 0x8263:
            raise MeshError("Invalid CTL range status")
        elif opcode == 0x8266 and len(payload) in (4, 9):
            self.state["kelvin"] = int.from_bytes(payload[:2], "little")
        else:
            self.coordinator.record(
                {"kind": "unknown_sig", "opcode": opcode, "payload": payload.hex()}
            )

    def model_address(self, model, company=None):
        node = self.options.get("node_address", 0x00B0)
        if self.composition:
            for index, element in enumerate(self.composition["elements"]):
                if company is None and model in element["sig_models"]:
                    return node + index
                if company is not None and any(
                    tuple(item) == (company, model) for item in element["vendor_models"]
                ):
                    return node + index
        return node

    async def _request(
        self,
        payload,
        opcode,
        company=None,
        *,
        device=False,
        dp_match=None,
        retries=0,
        model=None,
        access_match=None,
    ):
        if not self.ensure_connection or self.mode == "observe":
            raise MeshError("Configure existing mesh keys and a unique source address first")
        await self.ensure_connection()
        controller = self.controller
        if controller is None:
            raise MeshError("No active mesh connection")
        destination = self.model_address(model, company) if model is not None else controller.node

        def matches(response):
            if (
                response.source != destination
                or response.device_key != device
                or response.access.opcode != opcode
                or response.access.company != company
            ):
                return False
            if access_match is not None and not access_match(response.access.payload):
                return False
            if dp_match is not None:
                try:
                    reports = decode_report(response.access.payload)
                except (MeshError, TuyaDPError):
                    return False
                return dp_match(reports)
            return True

        try:
            response = await controller.request(
                payload, matches, device=device, retries=retries, destination=destination
            )
            self.coordinator.record(
                {
                    "kind": "command_result",
                    "opcode": opcode,
                    "company": company,
                    "src": response.source,
                    "response_seq": response.sequence,
                    "matched": True,
                }
            )
            return response
        except MeshTimeoutError:
            self.coordinator.data["command_failures"] = (
                self.coordinator.data.get("command_failures", 0) + 1
            )
            if self.coordinator.data["command_failures"] >= 3:
                self.coordinator.data["responsive"] = False
            self.coordinator.async_set_updated_data(dict(self.coordinator.data))
            raise

    async def query(self, ids=(0,)):
        return await self._request(
            query(tuple(ids)),
            DATA,
            COMPANY_ID,
            model=4,
            dp_match=lambda reports: (
                bool(reports)
                if tuple(ids) == (0,)
                else all(any(dp.id == i for dp in reports) for i in ids)
            ),
            retries=1,
        )

    async def discover(self):
        if self.mode == "observe":
            raise MeshError("Discovery queries require configured existing mesh")
        results = {}
        requests: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
        if self.options.get("device_key"):
            requests.append(
                (
                    "composition",
                    lambda: self._request(bytes.fromhex("800800"), 2, device=True, retries=1),
                )
            )
        requests.append(("vendor_status", lambda: self.query()))
        if self.mode == "sig":
            requests.extend(
                [
                    (
                        "power",
                        lambda: self._request(
                            bytes.fromhex("8201"), 0x8204, retries=1, model=0x1000
                        ),
                    ),
                    (
                        "brightness",
                        lambda: self._request(
                            bytes.fromhex("824b"), 0x824E, retries=1, model=0x1300
                        ),
                    ),
                    (
                        "temperature_range",
                        lambda: self._request(
                            bytes.fromhex("8262"), 0x8263, retries=1, model=0x1303
                        ),
                    ),
                    (
                        "temperature",
                        lambda: self._request(
                            bytes.fromhex("8261"), 0x8266, retries=1, model=0x1306
                        ),
                    ),
                    (
                        "hsl",
                        lambda: self._request(
                            bytes.fromhex("826d"), 0x8278, retries=1, model=0x1307
                        ),
                    ),
                ]
            )
        for name, request in requests:
            try:
                await request()
                results[name] = "response_received"
            except MeshError as err:
                results[name] = type(err).__name__
        self.coordinator.record({"kind": "discovery", "results": results})
        return results

    async def write_dp(self, dp):
        if self.mode != "vendor_dp" or not self.has_dp(dp.id, dp.type) or dp.id not in range(1, 8):
            raise MeshError("DP writes require an observed supported DP and vendor mode")
        if dp.id == 1 and dp.type != 1:
            raise MeshError("Power must be boolean")
        if dp.id == 3 and (dp.type != 2 or not 10 <= dp.value <= 1000):
            raise MeshError("Brightness outside range")
        if dp.id == 4 and (not self.supports_temperature or not 0 <= dp.value <= 1000):
            raise MeshError("Color temperature metadata not confirmed")
        if dp.id == 5 and not self.supports_color:
            raise MeshError("Color encoding not confirmed")
        if dp.id == 6 and dp.diagnostics() not in self.scenes.values():
            raise MeshError("Only captured, explicitly enabled scenes may be replayed")
        if dp.id == 7 and not self.countdown_range:
            raise MeshError("Countdown units/range not confirmed")
        if dp.id == 7 and not 0 <= dp.value <= self.countdown_range:
            raise MeshError("Countdown outside confirmed range")
        if dp.id == 2 and dp.diagnostics() not in self.history.get("2", []):
            raise MeshError("Only observed modes can be selected")
        return await self._request(
            write([dp]),
            DATA,
            COMPANY_ID,
            model=4,
            dp_match=lambda reports: any(report == dp for report in reports),
        )

    @property
    def countdown_range(self):
        maximum = self.options.get("countdown_max_seconds", 0)
        return (
            maximum
            if self.options.get("countdown_seconds_confirmed") and self.has_dp(7, 2) and maximum > 0
            else None
        )

    async def next_tid(self):
        async with self.tid_lock:
            self.tid = (self.tid + 1) & 255
            delay = 6 - (time.monotonic() - self.tid_last[self.tid])
            if delay > 0:
                await asyncio.sleep(delay)
            self.tid_last[self.tid] = time.monotonic()
            return self.tid

    async def set_power(self, on):
        if not self.supports_power:
            raise MeshError("No observed power capability")
        if self.mode == "vendor_dp":
            return await self.write_dp(DP(1, 1, bytes((bool(on),))))
        payload = bytes.fromhex("8202") + bytes((bool(on), await self.next_tid()))
        return await self._request(
            payload,
            0x8204,
            model=0x1000,
            access_match=lambda data: (
                len(data) in (1, 3) and data[1 if len(data) == 3 else 0] == int(on)
            ),
        )

    async def set_brightness(self, value):
        from .protocol.conversions import brightness_to_tuya

        if not self.supports_brightness:
            raise MeshError("No observed brightness capability")
        if self.mode == "vendor_dp":
            return await self.write_dp(DP(3, 2, brightness_to_tuya(value).to_bytes(4, "big")))
        level = (max(0, min(255, value)) * 65535 + 127) // 255
        return await self._request(
            bytes.fromhex("824c") + level.to_bytes(2, "little") + bytes((await self.next_tid(),)),
            0x824E,
            model=0x1300,
            access_match=lambda data: (
                len(data) in (2, 5)
                and int.from_bytes(data[2:4] if len(data) == 5 else data[:2], "little") == level
            ),
        )

    async def set_temperature(self, kelvin):
        if not self.supports_temperature:
            raise MeshError("Temperature range/report not confirmed")
        lo, hi = self.temperature_range
        kelvin = max(lo, min(hi, int(kelvin)))
        if self.mode == "vendor_dp":
            raw = ((kelvin - lo) * 1000 + (hi - lo) // 2) // (hi - lo)
            return await self.write_dp(DP(4, 2, raw.to_bytes(4, "big")))
        return await self._request(
            bytes.fromhex("8264")
            + kelvin.to_bytes(2, "little")
            + bytes(2)
            + bytes((await self.next_tid(),)),
            0x8266,
            model=0x1306,
            access_match=lambda data: (
                len(data) in (4, 9)
                and int.from_bytes(data[4:6] if len(data) == 9 else data[:2], "little") == kelvin
            ),
        )

    async def set_color(self, hs, brightness):
        if not self.supports_color:
            raise MeshError("Color encoding/report not confirmed")
        if self.mode == "vendor_dp":
            return await self.write_dp(
                DP(
                    5,
                    3,
                    encode_hsv_string(
                        hs[0], hs[1], max(0, min(255, brightness)) * 100 / 255
                    ).encode(),
                )
            )
        import colorsys

        rgb = colorsys.hsv_to_rgb(hs[0] / 360, hs[1] / 100, brightness / 255)
        hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
        values = [round(lightness * 65535), round(hue * 65535), round(saturation * 65535)]
        return await self._request(
            bytes.fromhex("8276")
            + b"".join(n.to_bytes(2, "little") for n in values)
            + bytes((await self.next_tid(),)),
            0x8278,
            model=0x1307,
            access_match=lambda data: (
                len(data) in (6, 7)
                and data[:6] == b"".join(n.to_bytes(2, "little") for n in values)
            ),
        )

    async def bind_vendor(self, app_key_index):
        if not self.options.get("device_key") or not 0 <= app_key_index <= 4095:
            raise MeshError("Device key and valid existing AppKey index required")
        element = self.model_address(4, COMPANY_ID)
        parameters = (
            element.to_bytes(2, "little")
            + app_key_index.to_bytes(2, "little")
            + COMPANY_ID.to_bytes(2, "little")
            + bytes.fromhex("0400")
        )
        response = await self._request(bytes.fromhex("803d") + parameters, 0x803E, device=True)
        payload = response.access.payload
        if len(payload) != 9 or payload[1:] != parameters or payload[0] != 0:
            raise MeshError("Vendor model binding was not confirmed successful")
        self.coordinator.record(
            {
                "kind": "binding",
                "status": 0,
                "element": element,
                "company": COMPANY_ID,
                "model": 4,
                "app_key_index": app_key_index,
            }
        )

    async def select_light_mode(self, kind):
        value = self.options.get(f"{kind}_mode_value", -1)
        if self.mode == "vendor_dp" and value >= 0:
            await self.write_dp(DP(2, 4, bytes((value,))))
