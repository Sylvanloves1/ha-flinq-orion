"""UI onboarding: a mesh network entry with one subentry per lamp (D-014, D-015).

Lamps join with the keys of an existing mesh network, or are paired by this integration
after a factory reset (D-022).
"""

import asyncio
from hmac import compare_digest
import re
from types import MappingProxyType
from typing import Any, cast
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol
from . import pairing
from .const import DEFAULT_LAMP_NAME, DOMAIN, NETWORK_TITLE, PROXY_SERVICE, SUBENTRY_LAMP
from .credential_import import (
    PROVIDER_DOMAIN,
    matching_entries,
    import_credentials,
    verify_network_evidence,
    CredentialImportError,
)
from .protocol.advertisements import identity_material
from .protocol.errors import FlinqOrionError
from .protocol.provisioning import ProvisioningError

MAC_ADDRESS = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")
HEX_KEY = re.compile(r"[0-9a-fA-F]{32}")

# Replaced by the Tuya schema (DP 2 enum indices, DP 7 in seconds), see D-011.
SUPERSEDED_OPTIONS = (
    "white_mode_value",
    "color_mode_value",
    "scene_mode_value",
    "countdown_seconds_confirmed",
    "countdown_max_seconds",
)
# Per lamp since 0.4.0: stored in the lamp subentry, not in the network options.
LAMP_OPTIONS = ("device_key", "node_address")


def lamp_subentries(entry: ConfigEntry) -> dict[str, ConfigSubentry]:
    return {
        subentry_id: subentry
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_LAMP
    }


def configured_lamp(hass, address: str) -> ConfigEntry | None:
    """The network entry that already holds this lamp."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        addresses = {subentry.data.get("address") for subentry in entry.subentries.values()}
        addresses.add(entry.data.get("address"))  # version 1 entry, not migrated yet
        if address in addresses:
            return entry
    return None


def tuya_entries(hass, address: str):
    return matching_entries(hass.config_entries.async_entries(PROVIDER_DOMAIN), address)


def is_network_id(proxy_data, net_key: str) -> bool:
    return bool(
        proxy_data
        and len(proxy_data) == 9
        and proxy_data[0] == 0
        and compare_digest(proxy_data[1:], identity_material(bytes.fromhex(net_key))[0])
    )


def network_for(hass, address: str, service_data) -> ConfigEntry | None:
    """Configured network of a lamp: from its Network ID, or from a tuya_ble_mesh entry.

    A lamp in its low-power cycle advertises only Node Identity, which needs the unknown
    mesh address; the tuya_ble_mesh entry then still identifies the network.
    """
    proxy = service_data.get(PROXY_SERVICE)
    imported = {
        str({**entry.data, **entry.options}.get("net_key", "")).lower()
        for entry in tuya_entries(hass, address)
    }
    for entry in hass.config_entries.async_entries(DOMAIN):
        net_key = entry.options.get("net_key")
        if net_key and (net_key.lower() in imported or is_network_id(proxy, net_key)):
            return entry
    return None


def lamp_data(hass, network: ConfigEntry | None, address: str) -> dict[str, Any]:
    """Subentry data. The mesh address is detected on the first connection, unless
    tuya_ble_mesh holds this lamp in the same network: then its address and DeviceKey are used.
    """
    data: dict[str, Any] = {"address": address, "node_address": None}
    net_key = str(network.options.get("net_key", "")).lower() if network else ""
    for entry in tuya_entries(hass, address):
        try:
            credentials, _ = import_credentials(entry, address)
        except CredentialImportError:
            continue
        if net_key and credentials["net_key"] == net_key:
            data["node_address"] = credentials["node_address"]
            if credentials["device_key"]:
                data["device_key"] = credentials["device_key"]
            break
    return data


def nearby_lamps(hass, network: ConfigEntry) -> dict[str, str]:
    """Unconfigured mesh proxies nearby, with the lamps of this network first."""
    net_key = network.options.get("net_key")
    matched, unverified = {}, {}
    for info in bluetooth.async_discovered_service_info(hass, connectable=False):
        address = info.address.upper()
        proxy = info.service_data.get(PROXY_SERVICE)
        if not proxy or configured_lamp(hass, address):
            continue
        if net_key and is_network_id(proxy, net_key):
            matched[address] = f"{address} ({info.rssi} dBm, this network)"
        else:
            unverified[address] = f"{address} ({info.rssi} dBm, network not verified)"
    return {**matched, **unverified}


def lamp_name(user_input) -> str:
    return str(user_input.get("name", "")).strip() or DEFAULT_LAMP_NAME


def configured_addresses(hass) -> set[str]:
    addresses = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        addresses |= {subentry.data.get("address") for subentry in entry.subentries.values()}
        addresses.add(entry.data.get("address"))
    addresses.discard(None)
    return addresses


def session_of(entry: ConfigEntry):
    """Running mesh session of a loaded network with keys, else None."""
    runtime = getattr(entry, "runtime_data", None)
    return getattr(runtime, "session", None)


def pairing_networks(hass) -> list[ConfigEntry]:
    return [entry for entry in hass.config_entries.async_entries(DOMAIN) if session_of(entry)]


def paired_lamp_data(outcome: pairing.PairingOutcome) -> dict[str, Any]:
    return {
        "address": outcome.address,
        "node_address": outcome.unicast,
        "device_key": outcome.device_key,
        "configured": outcome.configured,
        "paired": True,
    }


# Reasons of ProvisioningError with their own abort text; others use "pair_failed".
PAIR_REASONS = frozenset(
    {"not_found", "timeout", "oob_required", "confirmation_failed", "cannot_assign_addresses"}
)


class PairingSteps:
    """Steps shared by the network flow and the "Add lamp" flow (D-022).

    The subclass provides `_pairing_network()` and `async_step_pair_finish()`.
    """

    hass: Any
    _address: str | None = None
    _name: str = DEFAULT_LAMP_NAME
    _task: asyncio.Task | None = None
    _outcome: pairing.PairingOutcome | None = None
    _reason: str = "pair_failed"
    _pair_network: pairing.Network | None = None

    async def _pairing_network(self) -> pairing.Network:
        raise NotImplementedError

    async def async_step_pair(self, user_input=None):
        errors = {}
        # A discovered lamp has its address already; otherwise list lamps in pairing mode.
        lamps = (
            {}
            if self._address
            else pairing.pairable_lamps(self.hass, configured_addresses(self.hass))
        )
        if user_input is not None:
            self._name = lamp_name(user_input)
            address = user_input.get("address") or self._address
            if address and (address == self._address or address in lamps):
                self._address = address
                return await self.async_step_pair_progress()
            errors["base"] = "no_lamp_found"
        schema: dict[Any, Any] = {}
        if self._address is None and lamps:
            schema[vol.Required("address")] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=address, label=f"{address} ({info.rssi} dBm)"
                        )
                        for address, info in lamps.items()
                    ],
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        schema[vol.Required("name", default=self._name)] = str
        return cast(Any, self).async_show_form(
            step_id="pair",
            data_schema=cast(Any, vol.Schema(schema)),
            errors=errors,
            description_placeholders={"address": self._address or "-"},
        )

    async def async_step_pair_progress(self, user_input=None):
        if self._task is None:
            self._task = self.hass.async_create_task(self._run_pairing())
        if not self._task.done():
            return cast(Any, self).async_show_progress(
                step_id="pair_progress", progress_action="pairing", progress_task=self._task
            )
        try:
            self._outcome = self._task.result()
        except ProvisioningError as err:
            self._reason = f"pair_{err.reason}" if err.reason in PAIR_REASONS else "pair_failed"
            return cast(Any, self).async_show_progress_done(next_step_id="pair_failed")
        except Exception:
            self._reason = "pair_failed"
            return cast(Any, self).async_show_progress_done(next_step_id="pair_failed")
        return cast(Any, self).async_show_progress_done(next_step_id="pair_finish")

    async def _run_pairing(self) -> pairing.PairingOutcome:
        self._pair_network = await self._pairing_network()
        assert self._address is not None
        return await pairing.pair(self.hass, self._pair_network, self._address)

    async def _forget_pending(self):
        """The subentry now holds the DeviceKey; the pending record is no longer needed."""
        if self._pair_network and self._outcome:
            await self._pair_network.store.remove_pending(self._outcome.address)

    async def async_step_pair_failed(self, user_input=None):
        return cast(Any, self).async_abort(reason=self._reason)


class ConfigFlow(PairingSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    _address: str | None = None
    _network: ConfigEntry | None = None
    _keys: tuple[bytes, bytes] | None = None

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {SUBENTRY_LAMP: LampFlow}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Offer only lamps that are identified: of a configured network, or held by
        tuya_ble_mesh when no network is configured yet (D-015)."""
        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        if configured_lamp(self.hass, address):
            # Also a lamp of a network in pairing mode after a power cycle: never pair it
            # again, it restores its network after the pairing window (D-022).
            return self.async_abort(reason="already_configured")
        self._address = address
        self.context["title_placeholders"] = {"name": address}
        if pairing.pairable(discovery_info):
            return await self.async_step_pair_network()
        network = network_for(self.hass, address, discovery_info.service_data)
        if network is not None:
            self._network = network
            return await self.async_step_add_lamp()
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="not_in_network")
        if not tuya_entries(self.hass, address):
            return self.async_abort(reason="not_identified")
        return await self.async_step_manual()

    async def async_step_add_lamp(self, user_input=None) -> ConfigFlowResult:
        network, address = self._network, self._address
        assert network is not None and address is not None
        if user_input is not None:
            if configured_lamp(self.hass, address):
                return self.async_abort(reason="already_configured")
            self.hass.config_entries.async_add_subentry(
                network,
                ConfigSubentry(
                    data=MappingProxyType(lamp_data(self.hass, network, address)),
                    subentry_type=SUBENTRY_LAMP,
                    title=lamp_name(user_input),
                    unique_id=address,
                ),
            )
            return self.async_abort(
                reason="lamp_added", description_placeholders={"network": network.title}
            )
        return self.async_show_form(
            step_id="add_lamp",
            data_schema=cast(
                Any, vol.Schema({vol.Required("name", default=DEFAULT_LAMP_NAME): str})
            ),
            description_placeholders={"address": address, "network": network.title},
        )

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["pair_network", "manual"])

    async def async_step_pair_network(self, user_input=None) -> ConfigFlowResult:
        """Pair into a running network, or into a new network with generated keys."""
        networks = {entry.entry_id: entry for entry in pairing_networks(self.hass)}
        if user_input is None and self.source == config_entries.SOURCE_BLUETOOTH:
            if len(networks) == 1:
                # A lamp found in pairing mode joins the only network (D-022).
                self._network = next(iter(networks.values()))
                return await self.async_step_pair()
        if user_input is not None or not networks:
            choice = (user_input or {}).get("network", "new")
            self._network = networks.get(choice)
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="pair_network",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        vol.Required("network"): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    selector.SelectOptionDict(value=entry_id, label=entry.title)
                                    for entry_id, entry in networks.items()
                                ]
                                + [selector.SelectOptionDict(value="new", label="+")],
                                translation_key="network",
                                mode=selector.SelectSelectorMode.LIST,
                            )
                        )
                    }
                ),
            ),
        )

    async def _pairing_network(self) -> pairing.Network:
        if self._network is not None and (session := session_of(self._network)):
            return await pairing.network_from_session(self.hass, session)
        self._keys = pairing.new_keys()
        return await pairing.new_network(self.hass, *self._keys)

    async def async_step_pair_finish(self, user_input=None) -> ConfigFlowResult:
        outcome = self._outcome
        assert outcome is not None
        data = paired_lamp_data(outcome)
        if self._network is not None:
            self.hass.config_entries.async_add_subentry(
                self._network,
                ConfigSubentry(
                    data=MappingProxyType(data),
                    subentry_type=SUBENTRY_LAMP,
                    title=self._name,
                    unique_id=outcome.address,
                ),
            )
            await self._forget_pending()
            return self.async_abort(
                reason="lamp_paired", description_placeholders={"network": self._network.title}
            )
        assert self._keys is not None
        net_key, app_key = self._keys
        await self.async_set_unique_id(identity_material(net_key)[0].hex(), raise_on_progress=False)
        await self._forget_pending()
        return self.async_create_entry(
            title=NETWORK_TITLE,
            data={},
            options={
                "transport_mode": "sig",
                "connection_mode": "on_demand",
                "net_key": net_key.hex(),
                "app_key": app_key.hex(),
                "iv_index": 0,
                # A new network: no other controller has ever used this address.
                "source_address": 1,
                "new_source_confirmed": True,
                "availability_timeout": 300,
            },
            subentries=[
                ConfigSubentryData(
                    data=data,
                    subentry_type=SUBENTRY_LAMP,
                    title=self._name,
                    unique_id=outcome.address,
                )
            ],
        )

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            address = str(user_input["address"]).strip().upper()
            if not MAC_ADDRESS.fullmatch(address):
                errors["address"] = "invalid_address"
            elif configured_lamp(self.hass, address):
                return self.async_abort(reason="already_configured")
            else:
                self._address = address
                info = bluetooth.async_last_service_info(self.hass, address, connectable=False)
                network = network_for(self.hass, address, info.service_data if info else {})
                if network is not None:
                    # A lamp of a configured network joins it; one entry per network.
                    self._network = network
                    return await self.async_step_add_lamp(user_input)
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=NETWORK_TITLE,
                    data={},
                    subentries=[
                        ConfigSubentryData(
                            data=lamp_data(self.hass, None, address),
                            subentry_type=SUBENTRY_LAMP,
                            title=lamp_name(user_input),
                            unique_id=address,
                        )
                    ],
                )
        return self.async_show_form(
            step_id="manual",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        vol.Required("address", default=self._address or ""): str,
                        vol.Required("name", default=DEFAULT_LAMP_NAME): str,
                    }
                ),
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class LampFlow(PairingSteps, config_entries.ConfigSubentryFlow):
    """Pair or add a lamp of this network, or change its settings and configuration."""

    async def async_step_user(self, user_input=None) -> SubentryFlowResult:
        options = ["existing"]
        if session_of(self._get_entry()):
            options.insert(0, "pair")
            if await self._restorable():
                options.append("restore")
        return self.async_show_menu(step_id="user", menu_options=options)

    async def _pairing_network(self) -> pairing.Network:
        session = session_of(self._get_entry())
        if session is None:
            raise ProvisioningError("not_found", "The network has no running mesh session")
        return await pairing.network_from_session(self.hass, session)

    async def async_step_pair_finish(self, user_input=None) -> SubentryFlowResult:
        outcome = self._outcome
        assert outcome is not None
        await self._forget_pending()
        return self.async_create_entry(
            title=self._name, data=paired_lamp_data(outcome), unique_id=outcome.address
        )

    async def _restorable(self) -> list[dict]:
        """Pairings that ended after Complete without a lamp subentry (restart, crash)."""
        session = session_of(self._get_entry())
        if session is None:
            return []
        store = pairing.PairingStore(self.hass, session.key_scope)
        await store.load()
        known = configured_addresses(self.hass)
        return [record for record in store.pending if record["address"] not in known]

    async def async_step_restore(self, user_input=None) -> SubentryFlowResult:
        records = {record["address"]: record for record in await self._restorable()}
        if user_input is not None and user_input["address"] in records:
            record = records[user_input["address"]]
            return self.async_create_entry(
                title=lamp_name(user_input),
                data={
                    "address": record["address"],
                    "node_address": record["unicast"],
                    "device_key": record["device_key"],
                    "configured": False,
                    "paired": True,
                },
                unique_id=record["address"],
            )
        if not records:
            return self.async_abort(reason="nothing_to_restore")
        return self.async_show_form(
            step_id="restore",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        vol.Required("address"): vol.In(sorted(records)),
                        vol.Required("name", default=DEFAULT_LAMP_NAME): str,
                    }
                ),
            ),
        )

    async def async_step_existing(self, user_input=None) -> SubentryFlowResult:
        network = self._get_entry()
        errors = {}
        if user_input is not None:
            address = str(user_input["address"]).strip().upper()
            if not MAC_ADDRESS.fullmatch(address):
                errors["address"] = "invalid_address"
            elif configured_lamp(self.hass, address):
                errors["address"] = "lamp_configured"
            else:
                return self.async_create_entry(
                    title=lamp_name(user_input),
                    data=lamp_data(self.hass, network, address),
                    unique_id=address,
                )
        nearby = nearby_lamps(self.hass, network)
        address_field: Any = str
        if nearby:
            address_field = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=address, label=label)
                        for address, label in nearby.items()
                    ],
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        return self.async_show_form(
            step_id="existing",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        vol.Required("address"): address_field,
                        vol.Required("name", default=DEFAULT_LAMP_NAME): str,
                    }
                ),
            ),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is None and subentry.data.get("configured", True) is False:
            return self.async_show_menu(step_id="reconfigure", menu_options=["finish", "settings"])
        return await self.async_step_settings(user_input)

    async def async_step_finish(self, user_input=None) -> SubentryFlowResult:
        """Repeat AppKey Add, binds and publication over the normal network link."""
        if self._task is None:
            self._task = self.hass.async_create_task(self._finish())
        if not self._task.done():
            return self.async_show_progress(
                step_id="finish", progress_action="configuring", progress_task=self._task
            )
        try:
            configured = self._task.result()
        except (FlinqOrionError, TimeoutError):
            configured = False
        return self.async_show_progress_done(
            next_step_id="finish_done" if configured else "finish_failed"
        )

    async def _finish(self) -> bool:
        network = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        session = session_of(network)
        lamp = network.runtime_data.lamps.get(subentry.subentry_id) if session else None
        if session is None or lamp is None:
            return False
        result = await pairing.finish_configuration(session, lamp.coordinator)
        return result.configured

    async def async_step_finish_done(self, user_input=None) -> SubentryFlowResult:
        network = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        return self.async_update_and_abort(
            network, subentry, data={**subentry.data, "configured": True}, reason="configured"
        )

    async def async_step_finish_failed(self, user_input=None) -> SubentryFlowResult:
        return self.async_abort(reason="finish_failed")

    async def async_step_settings(self, user_input=None) -> SubentryFlowResult:
        network = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors = {}
        if user_input is not None:
            device_key = str(user_input.get("device_key", "")).strip().lower()
            node = user_input.get("node_address") or None
            if device_key and (not HEX_KEY.fullmatch(device_key) or device_key == "0" * 32):
                errors["device_key"] = "invalid_key"
            if node is not None and node == network.options.get("source_address"):
                errors["node_address"] = "invalid_node"
            if not errors:
                data = {**subentry.data, "node_address": node}
                data.pop("device_key", None)
                if device_key:
                    data["device_key"] = device_key
                node_changed = node != subentry.data.get("node_address")
                result = self.async_update_and_abort(
                    network, subentry, title=lamp_name(user_input), data=data
                )
                if node_changed:
                    # The update listener skips address changes (detection needs no reload).
                    self.hass.config_entries.async_schedule_reload(network.entry_id)
                return result
        return self.async_show_form(
            step_id="settings",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        vol.Required("name", default=subentry.title): str,
                        vol.Optional(
                            "node_address", default=subentry.data.get("node_address") or 0
                        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=0x7FFF)),
                        vol.Optional(
                            "device_key", default=subentry.data.get("device_key", "")
                        ): selector.TextSelector(
                            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                        ),
                    }
                ),
            ),
            errors=errors,
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Network settings: keys, controller address, protocol and connection mode."""

    _mesh: dict | None = None
    _import_defaults: dict | None = None
    _previous_source: int | None = None
    # (subentry ID, mesh address and DeviceKey) of the lamp whose credentials were imported.
    _lamp_import: tuple[str, dict] | None = None

    def _lamps(self):
        return lamp_subentries(self.config_entry)

    async def async_step_init(self, user_input=None):
        errors = {}
        old = {**self.config_entry.options, **(self._import_defaults or {})}
        lamps = self._lamps()
        providers = self.hass.config_entries.async_entries(PROVIDER_DOMAIN)
        candidates: dict[str, tuple[ConfigEntry, str]] = {}
        for subentry_id, subentry in lamps.items():
            for entry in matching_entries(providers, subentry.data["address"]):
                candidates.setdefault(entry.entry_id, (entry, subentry_id))
        if user_input is not None and user_input.get("import_entry_id"):
            selected = candidates.get(user_input["import_entry_id"])
            if selected is None:
                errors["base"] = "import_wrong_device"
            else:
                entry, subentry_id = selected
                address = lamps[subentry_id].data["address"]
                runtime = getattr(self.config_entry, "runtime_data", None)
                lamp = runtime.lamps.get(subentry_id) if runtime is not None else None
                try:
                    credentials, previous_source = import_credentials(entry, address)
                    info = bluetooth.async_last_service_info(self.hass, address, connectable=False)
                    records = list(lamp.coordinator.records) if lamp is not None else []
                    verified = verify_network_evidence(
                        credentials, info.service_data if info else {}, records
                    )
                except CredentialImportError as err:
                    errors["base"] = err.reason
                else:
                    lamp_values = {key: credentials.pop(key) for key in LAMP_OPTIONS}
                    self._lamp_import = subentry_id, lamp_values
                    self._import_defaults = {
                        **credentials,
                        "transport_mode": "query_only",
                        "source_address": 0,
                        "new_source_confirmed": False,
                        "imported_source_address": previous_source,
                    }
                    self._previous_source = previous_source
                    if lamp is not None:
                        lamp.coordinator.record(
                            {
                                "kind": "credential_import",
                                "network_identity_verified": verified,
                                "previous_controller_address": previous_source,
                            }
                        )
                    return await self.async_step_init()
        elif user_input is not None:
            user_input = {
                key: value for key, value in user_input.items() if key != "import_entry_id"
            }
            for key in ("net_key", "app_key"):
                value = user_input.get(key, "").strip()
                user_input[key] = value
                if value and (not HEX_KEY.fullmatch(value) or value == "0" * 32):
                    errors[key] = "invalid_key"
            if bool(user_input.get("net_key")) != bool(user_input.get("app_key")):
                errors["base"] = "both_keys"
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if (
                    entry.entry_id != self.config_entry.entry_id
                    and user_input.get("net_key")
                    and entry.options.get("net_key", "").lower() == user_input["net_key"].lower()
                ):
                    errors["net_key"] = "network_in_use"
            mode = user_input.get("transport_mode", "observe")
            if mode != "observe":
                if not user_input.get("net_key") or not user_input.get("app_key"):
                    errors["base"] = "keys_required"
                source = user_input.get("source_address", 0)
                nodes = {subentry.data.get("node_address") for subentry in lamps.values()}
                if self._lamp_import:
                    nodes.add(self._lamp_import[1]["node_address"])
                if (
                    not 1 <= source <= 0x7FFF
                    or source in nodes
                    or source == self._previous_source
                    or source == old.get("imported_source_address")
                ):
                    errors["source_address"] = "invalid_source"
                if (
                    source != old.get("source_address")
                    or user_input.get("net_key") != old.get("net_key")
                    or old.get("transport_mode", "observe") == "observe"
                ):
                    if not user_input.get("new_source_confirmed"):
                        errors["new_source_confirmed"] = "confirm_unique_source"
            if not errors:
                self._mesh = {**old, **user_input}
                return await self.async_step_profile()

        def optional(name, default):
            return vol.Optional(name, default=old.get(name, default))

        schema = vol.Schema(
            {
                vol.Optional("import_entry_id", default=""): vol.In(
                    {"": "-", **{key: entry.title for key, (entry, _) in candidates.items()}}
                ),
                vol.Required(
                    "transport_mode", default=old.get("transport_mode", "observe")
                ): vol.In(["observe", "query_only", "vendor_dp", "sig"]),
                vol.Required(
                    "connection_mode", default=old.get("connection_mode", "on_demand")
                ): vol.In(["on_demand", "persistent"]),
                optional("net_key", ""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                optional("app_key", ""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                optional("source_address", 0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=0x7FFF)
                ),
                optional("new_source_confirmed", False): bool,
                optional("iv_index", 0): vol.All(vol.Coerce(int), vol.Range(min=0, max=0xFFFFFFFF)),
                optional("availability_timeout", 300): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=cast(Any, schema),
            errors=errors,
            description_placeholders={
                "previous_source": str(
                    self._previous_source or old.get("imported_source_address") or "-"
                )
            },
        )

    async def async_step_profile(self, user_input=None):
        old = self._mesh or dict(self.config_entry.options)
        errors = {}
        if user_input is not None:
            if (
                user_input.get("ct_range_confirmed")
                and not 800
                <= user_input.get("min_kelvin", 0)
                < user_input.get("max_kelvin", 0)
                <= 20000
            ):
                errors["base"] = "invalid_ct_range"
            if not errors:
                data = {**old, **user_input}
                for superseded in (*SUPERSEDED_OPTIONS, *LAMP_OPTIONS):
                    data.pop(superseded, None)
                self._store_lamp_import()
                return self.async_create_entry(title="", data=data)

        def optional(name, default):
            return vol.Optional(name, default=old.get(name, default))

        return self.async_show_form(
            step_id="profile",
            data_schema=cast(
                Any,
                vol.Schema(
                    {
                        optional("hsv_encoding_confirmed", False): bool,
                        optional("ct_range_confirmed", False): bool,
                        optional("min_kelvin", 0): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=20000)
                        ),
                        optional("max_kelvin", 0): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=20000)
                        ),
                        optional("scene_replay_confirmed", False): bool,
                    }
                ),
            ),
            errors=errors,
        )

    def _store_lamp_import(self):
        """Write the imported mesh address and DeviceKey into the lamp subentry."""
        if not self._lamp_import:
            return
        subentry_id, values = self._lamp_import
        subentry = self._lamps().get(subentry_id)
        if subentry is None:
            return
        data = {**subentry.data, "node_address": values["node_address"]}
        data.pop("device_key", None)
        if values.get("device_key"):
            data["device_key"] = values["device_key"]
        self.hass.config_entries.async_update_subentry(self.config_entry, subentry, data=data)
