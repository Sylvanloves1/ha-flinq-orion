"""Pairing in the config flow and the "Add lamp" flow (D-022)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from custom_components.flinq_orion import pairing
from custom_components.flinq_orion.protocol.advertisements import PROVISIONING
from custom_components.flinq_orion.protocol.crypto import k3
from custom_components.flinq_orion.protocol.provisioning import ProvisioningError
from test_active_mesh import APP, NET
from test_ha import ADDRESS, config_flow, config_hass, lamp_flow, lamp_subentry, network

NEW = "DC:23:4F:00:00:02"
ORION_PAIRING = bytes.fromhex("dc234f0000025415") + b"key5wvvr" + bytes(2)


def hass_with(*networks):
    hass = config_hass(networks)
    hass.async_create_task = lambda coro: asyncio.get_running_loop().create_task(coro)
    return hass


def outcome(configured=True):
    return pairing.PairingOutcome(NEW, 0x10, 1, "ab" * 16, configured=configured)


def running(entry):
    entry.runtime_data = SimpleNamespace(session=SimpleNamespace(key_scope="scope"), lamps={})
    return entry


async def complete(flow, step, user_input):
    """Run a pairing step through its progress task; returns the next step result."""
    progress = await getattr(flow, f"async_step_{step}")(user_input)
    assert progress["type"] == "progress"
    await asyncio.gather(flow._task, return_exceptions=True)
    done = await getattr(
        flow, f"async_step_{step}_progress" if step == "pair" else "async_step_finish"
    )()
    assert done["type"] == "progress_done"
    return await getattr(flow, f"async_step_{done['step_id']}")()


@pytest.mark.asyncio
async def test_a_new_user_pairs_the_first_lamp_into_a_new_network():
    flow = config_flow(hass_with())
    flow.flow_id = "flow"
    store = SimpleNamespace(remove_pending=AsyncMock())
    with (
        patch.object(pairing, "pairable_lamps", return_value={NEW: SimpleNamespace(rssi=-50)}),
        patch.object(pairing, "new_keys", return_value=(NET, APP)),
        patch.object(pairing, "new_network", AsyncMock(return_value=SimpleNamespace(store=store))),
        patch.object(pairing, "pair", AsyncMock(return_value=outcome())) as pair,
    ):
        form = await flow.async_step_pair_network()
        assert form["step_id"] == "pair" and "address" in form["data_schema"].schema
        result = await complete(flow, "pair", {"address": NEW, "name": "Garden"})
    assert pair.await_args.args[2] == NEW
    assert result["type"] == "create_entry"
    assert result["options"]["net_key"] == NET.hex() and result["options"]["source_address"] == 1
    assert result["options"]["transport_mode"] == "sig"
    (lamp,) = result["subentries"]
    assert lamp["title"] == "Garden" and lamp["unique_id"] == NEW
    assert lamp["data"] == {
        "address": NEW,
        "node_address": 0x10,
        "device_key": "ab" * 16,
        "configured": True,
        "paired": True,
    }
    flow.async_set_unique_id.assert_awaited_with(k3(NET).hex(), raise_on_progress=False)
    store.remove_pending.assert_awaited_once_with(NEW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProvisioningError("timeout", "slow"), "pair_timeout"),
        (ProvisioningError("oob_required", "oob"), "pair_oob_required"),
        (ProvisioningError("invalid_format", "bad"), "pair_failed"),
        (OSError("radio"), "pair_failed"),
    ],
)
async def test_pairing_errors_end_the_flow_with_a_reason(error, reason):
    flow = config_flow(hass_with())
    flow.flow_id = "flow"
    with (
        patch.object(pairing, "pairable_lamps", return_value={NEW: SimpleNamespace(rssi=-50)}),
        patch.object(pairing, "new_keys", return_value=(NET, APP)),
        patch.object(pairing, "new_network", AsyncMock(return_value=SimpleNamespace())),
        patch.object(pairing, "pair", AsyncMock(side_effect=error)),
    ):
        result = await complete(flow, "pair", {"address": NEW, "name": "Garden"})
    assert result["type"] == "abort" and result["reason"] == reason


@pytest.mark.asyncio
async def test_no_lamp_in_pairing_mode_shows_an_error():
    flow = config_flow(hass_with())
    with patch.object(pairing, "pairable_lamps", return_value={}):
        form = await flow.async_step_pair({"name": "Garden"})
    assert (
        form["errors"] == {"base": "no_lamp_found"} and "address" not in form["data_schema"].schema
    )


def discovery(address=NEW):
    return SimpleNamespace(address=address, service_data={PROVISIONING: ORION_PAIRING}, raw=None)


@pytest.mark.asyncio
async def test_discovery_of_a_lamp_in_pairing_mode():
    home = running(network(lamp_subentry()))
    flow = config_flow(hass_with(home), "bluetooth")
    form = await flow.async_step_bluetooth(discovery())
    # One network: the lamp joins it; the address comes from the discovery.
    assert form["step_id"] == "pair" and list(form["data_schema"].schema) == ["name"]
    assert flow._network is home
    store = SimpleNamespace(remove_pending=AsyncMock())
    with (
        patch.object(
            pairing, "network_from_session", AsyncMock(return_value=SimpleNamespace(store=store))
        ),
        patch.object(pairing, "pair", AsyncMock(return_value=outcome(configured=False))),
    ):
        flow.flow_id = "flow"
        result = await complete(flow, "pair", {"name": "Porch"})
    assert result["reason"] == "lamp_paired"
    added = flow.hass.config_entries.async_add_subentry.call_args.args[1]
    assert added.title == "Porch" and added.data["configured"] is False

    # A configured lamp in pairing mode after a power cycle is never offered again.
    result = await config_flow(hass_with(home), "bluetooth").async_step_bluetooth(
        discovery(ADDRESS)
    )
    assert result["reason"] == "already_configured"
    # No network: a new network; two networks: the user chooses.
    result = await config_flow(hass_with(), "bluetooth").async_step_bluetooth(discovery())
    assert result["step_id"] == "pair"
    other = running(network())
    other.entry_id = "other"
    result = await config_flow(hass_with(home, other), "bluetooth").async_step_bluetooth(
        discovery()
    )
    assert result["step_id"] == "pair_network"


class Store:
    pending = [{"address": NEW, "unicast": 0x10, "elements": 1, "device_key": "cd" * 16}]

    def __init__(self, hass, scope):
        pass

    async def load(self):
        pass


@pytest.mark.asyncio
async def test_add_lamp_offers_pairing_and_restores_an_interrupted_pairing():
    home = running(network(lamp_subentry()))
    hass = hass_with(home)
    flow = lamp_flow(hass, home)
    flow.flow_id = "flow"
    with patch.object(pairing, "PairingStore", Store):
        menu = await flow.async_step_user()
        assert menu["menu_options"] == ["pair", "existing", "restore"]
        result = await flow.async_step_restore({"address": NEW, "name": "Porch"})
    assert result["type"] == "create_entry" and result["unique_id"] == NEW
    assert result["data"] == {
        "address": NEW,
        "node_address": 0x10,
        "device_key": "cd" * 16,
        "configured": False,
        "paired": True,
    }
    store = SimpleNamespace(remove_pending=AsyncMock())
    flow = lamp_flow(hass, home)
    flow.flow_id = "flow"
    with (
        patch.object(pairing, "pairable_lamps", return_value={NEW: SimpleNamespace(rssi=-60)}),
        patch.object(
            pairing, "network_from_session", AsyncMock(return_value=SimpleNamespace(store=store))
        ),
        patch.object(pairing, "pair", AsyncMock(return_value=outcome())),
    ):
        result = await complete(flow, "pair", {"address": NEW, "name": "Porch"})
    assert result["type"] == "create_entry" and result["data"]["configured"] is True
    store.remove_pending.assert_awaited_once_with(NEW)


@pytest.mark.asyncio
@pytest.mark.parametrize(("configured", "reason"), [(True, "configured"), (False, "finish_failed")])
async def test_finish_configuration_of_a_paired_lamp(configured, reason):
    subentry = lamp_subentry(node_address=0x10, device_key="cd" * 16, configured=False)
    home = running(network(subentry))
    home.runtime_data.lamps = {"lamp": SimpleNamespace(coordinator=Mock())}
    hass = hass_with(home)
    flow = lamp_flow(hass, home, "reconfigure", subentry)
    flow.flow_id = "flow"
    menu = await flow.async_step_reconfigure()
    assert menu["type"] == "menu" and menu["menu_options"] == ["finish", "settings"]
    result_value = pairing.ConfigurationResult(configured=configured)
    with patch.object(pairing, "finish_configuration", AsyncMock(return_value=result_value)):
        result = await complete(flow, "finish", None)
    assert result["reason"] == reason
    if configured:
        update = hass.config_entries.async_update_subentry.call_args.kwargs
        assert update["data"]["configured"] is True


def test_a_repair_issue_asks_to_finish_the_configuration():
    from custom_components.flinq_orion import sync_configuration_issues

    entry = network(
        lamp_subentry(subentry_id="done"),
        lamp_subentry(address=NEW, subentry_id="open", configured=False),
    )
    with (
        patch("custom_components.flinq_orion.ir.async_create_issue") as create,
        patch("custom_components.flinq_orion.ir.async_delete_issue") as delete,
    ):
        sync_configuration_issues(None, entry)
    assert [call.args[2] for call in create.call_args_list] == ["configuration_open"]
    assert create.call_args.kwargs["translation_placeholders"] == {"name": "Garden"}
    assert [call.args[2] for call in delete.call_args_list] == ["configuration_done"]


@pytest.mark.asyncio
async def test_pairing_forms_can_be_sent_to_the_frontend():
    # As HA 2026.10 sends a form to the frontend (helpers/data_entry_flow.py).
    import probatio
    from homeassistant.helpers import config_validation as cv

    home = running(network(lamp_subentry()))
    other = running(network())
    other.entry_id = "other"
    forms = []
    flow = config_flow(hass_with(home, other))
    forms.append(await flow.async_step_pair_network())
    with patch.object(pairing, "pairable_lamps", return_value={NEW: SimpleNamespace(rssi=-50)}):
        forms.append(await config_flow(hass_with()).async_step_pair())
    with patch.object(pairing, "PairingStore", Store):
        forms.append(await lamp_flow(hass_with(home), home).async_step_restore())
    for form in forms:
        assert form["type"] == "form"
        probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
