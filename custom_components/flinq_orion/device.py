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
from .device_profiles import ORION_DPS, FLINQ_ORION_FQC8274_PROFILE as PROFILE

# work_mode (DP 2) enum indices from the schema.
LIGHT_MODES = {"white": 0, "color": 1, "scene": 2}


class OrionDevice:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.state: dict = {}
        self.history: dict[str, list[dict]] = {}
        self.composition: dict | None = None
        self.controller = None
        self.ensure_connection = None
        # Set by the session: any request keeps an on-demand link open for its idle time.
        self.activity: Callable[[], None] | None = None
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
    def supports_settings(self):
        """Schema data points (DP 2, 7, 51–59) are read and written over the vendor model."""
        return self.mode in ("sig", "vendor_dp")

    def setting(self, identifier):
        return ORION_DPS[identifier].decode(self.dp(identifier))

    @property
    def sig_temperature_range(self):
        return self.state.get("temperature_range") if self.mode == "sig" else None

    @property
    def temperature_range(self):
        if self.sig_temperature_range:
            return self.sig_temperature_range
        lo, hi = self.options.get("min_kelvin", 0), self.options.get("max_kelvin", 0)
        if self.options.get("ct_range_confirmed") and 800 <= lo < hi <= 20000:
            return lo, hi
        return PROFILE.default_kelvin_range

    @property
    def supports_temperature(self):
        return self.supports_brightness and (
            self.mode == "sig" or (self.mode == "vendor_dp" and self.has_dp(4, 2))
        )

    @property
    def kelvin(self):
        if self.sig_temperature_range:
            return self.state.get("kelvin")
        raw = self.setting(4)
        if raw is None:
            return None
        lo, hi = self.temperature_range
        return lo + (raw * (hi - lo) + 500) // 1000

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
                    if dp.value == LIGHT_MODES["color"]:
                        self.state["last_color_mode"] = "hs"
                    elif dp.value == LIGHT_MODES["white"]:
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
        timeout=15,
        discovery=False,
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
                payload,
                matches,
                device=device,
                retries=retries,
                destination=destination,
                timeout=timeout,
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
            if discovery:
                raise
            self.coordinator.data["command_failures"] = (
                self.coordinator.data.get("command_failures", 0) + 1
            )
            if self.coordinator.data["command_failures"] >= 3:
                self.coordinator.data["responsive"] = False
            self.coordinator.async_set_updated_data(dict(self.coordinator.data))
            raise
        finally:
            if self.activity:
                self.activity()

    async def probe(self):
        """One bounded read-only status read; a timeout is not counted as a command failure."""
        if self.mode == "sig":
            return await self._request(
                bytes.fromhex("8201"), 0x8204, model=0x1000, timeout=2, discovery=True
            )
        return await self.query(discovery=True)

    async def query(self, ids=(0,), *, discovery=False):
        ids = tuple(ids)
        response = await self._request(
            query(ids),
            DATA,
            COMPANY_ID,
            model=4,
            dp_match=lambda reports: (
                ids == (0,)
                or not reports
                or all(any(dp.id == identifier for dp in reports) for identifier in ids)
            ),
            retries=0 if discovery else 1,
            # The Orion answered the first query of a link after 1.7 s (captures 0 and 1).
            timeout=5 if discovery else 15,
            discovery=discovery,
        )
        reports = decode_report(response.access.payload)
        self.coordinator.record(
            {
                "kind": "query_result",
                "requested_dps": list(ids),
                "reported_dps": [dp.id for dp in reports],
                "result": "dp_report" if reports else "empty_dp_report",
            }
        )
        return response

    async def discover(self, *, include_optional=False):
        if self.mode == "observe":
            raise MeshError("Discovery queries require configured existing mesh")

        async def request(*args, **kwargs):
            kwargs.update(timeout=2, retries=0, discovery=True)
            return await self._request(*args, **kwargs)

        results = {}
        requests: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
        if self.mode != "sig":
            requests.append(("vendor_status", lambda: self.query(discovery=True)))
        if self.mode == "sig":
            requests.extend(
                [
                    (
                        "power",
                        lambda: request(bytes.fromhex("8201"), 0x8204, retries=1, model=0x1000),
                    ),
                    (
                        "brightness",
                        lambda: request(bytes.fromhex("824b"), 0x824E, retries=1, model=0x1300),
                    ),
                    (
                        "temperature",
                        lambda: request(bytes.fromhex("8261"), 0x8266, retries=1, model=0x1306),
                    ),
                    (
                        "hsl",
                        lambda: request(bytes.fromhex("826d"), 0x8278, retries=1, model=0x1307),
                    ),
                    # Settings (DP 2, 7, 51–59) only exist as Tuya data points. 0.2.5 removed
                    # this query on a suspicion; the failures were the battery (D-011).
                    ("vendor_status", lambda: self.query(discovery=True)),
                ]
            )
        if include_optional and self.mode == "sig":
            requests.append(
                (
                    "temperature_range",
                    lambda: request(bytes.fromhex("8262"), 0x8263, model=0x1303),
                )
            )
        if include_optional and self.options.get("device_key"):
            requests.append(
                (
                    "composition",
                    lambda: request(bytes.fromhex("800800"), 2, device=True, retries=1),
                )
            )
        for name, perform_request in requests:
            try:
                response = await perform_request()
                results[name] = (
                    "empty_dp_report"
                    if name == "vendor_status" and not decode_report(response.access.payload)
                    else "response_received"
                )
            except MeshError as err:
                results[name] = type(err).__name__
        self.coordinator.record({"kind": "discovery", "results": results})
        return results

    async def write_dp(self, dp):
        """Vendor-mode light write (DP 1, 3, 4, 5, 6) with light-specific checks."""
        if (
            self.mode != "vendor_dp"
            or not self.has_dp(dp.id, dp.type)
            or dp.id not in (1, 3, 4, 5, 6)
        ):
            raise MeshError("Light DP writes require vendor mode and an observed light DP")
        if dp.id == 1 and dp.type != 1:
            raise MeshError("Power must be boolean")
        if dp.id == 3 and (dp.type != 2 or not 10 <= dp.value <= 1000):
            raise MeshError("Brightness outside range")
        if dp.id == 4 and (not self.supports_temperature or not 0 <= dp.value <= 1000):
            raise MeshError("Color temperature outside range")
        if dp.id == 5 and not self.supports_color:
            raise MeshError("Color encoding not confirmed")
        if dp.id == 6 and dp.diagnostics() not in self.scenes.values():
            raise MeshError("Only captured, explicitly enabled scenes may be replayed")
        return await self._write(dp, exact=True)

    async def write_setting(self, identifier, value):
        """Schema-validated write of a lamp setting in SIG or vendor mode (D-011)."""
        spec = ORION_DPS.get(identifier)
        if spec is None or not spec.writable or not self.supports_settings:
            raise MeshError("Not a writable setting of this lamp")
        return await self._write(spec.encode(value))

    async def _write(self, *dps, exact=False):
        """Vendor DP write; the lamp confirms with a DP report (possibly to a group address).

        Settings match on the DP id, because the lamp may report a clamped value.
        """
        wanted = {dp.id for dp in dps}
        return await self._request(
            write(list(dps)),
            DATA,
            COMPANY_ID,
            model=4,
            dp_match=lambda reports: (
                all(dp in reports for dp in dps)
                if exact
                else wanted <= {report.id for report in reports}
            ),
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
        payload = bytes.fromhex("8202") + bytes((bool(on), await self.next_tid(), 0, 0))
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
            bytes.fromhex("824c")
            + level.to_bytes(2, "little")
            + bytes((await self.next_tid(), 0, 0)),
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
        raw = ((kelvin - lo) * 1000 + (hi - lo) // 2) // (hi - lo)
        if self.mode == "vendor_dp":
            return await self.write_dp(DP(4, 2, raw.to_bytes(4, "big")))
        if not self.sig_temperature_range:
            # The lamp never answered CTL Temperature Range Get, so the SIG Kelvin scale is
            # unknown. Use the schema data point in white mode instead (D-011).
            return await self._write(
                ORION_DPS[2].encode("white"), ORION_DPS[4].encode(raw), exact=True
            )
        return await self._request(
            bytes.fromhex("8264")
            + kelvin.to_bytes(2, "little")
            + bytes(2)
            + bytes((await self.next_tid(), 0, 0)),
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
            + bytes((await self.next_tid(), 0, 0)),
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
        # SIG mode: colour goes through HSL Set; set_temperature writes white mode with DP 4.
        if self.mode == "vendor_dp":
            await self.write_setting(2, ORION_DPS[2].options[LIGHT_MODES[kind]])
