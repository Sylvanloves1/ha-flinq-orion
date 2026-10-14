from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock
import pytest
from homeassistant.config_entries import ConfigSubentry
from custom_components.flinq_orion.config_flow import OptionsFlow

ADDRESS = "DC:23:4F:6E:A0:12"


def lamp(node_address=176, **data):
    return ConfigSubentry(
        data=MappingProxyType({"address": ADDRESS, "node_address": node_address, **data}),
        subentry_id="lamp",
        subentry_type="lamp",
        title="Garden",
        unique_id=ADDRESS,
    )


class Flow(OptionsFlow):
    entry = SimpleNamespace(options={}, entry_id="target", data={}, subentries={"lamp": lamp()})

    @property
    def config_entry(self):
        return self.entry


def flow(providers=(), networks=()):
    f = Flow()
    f.entry = SimpleNamespace(options={}, entry_id="target", data={}, subentries={"lamp": lamp()})

    def entries(domain=None):
        return list(providers) if domain == "tuya_ble_mesh" else list(networks)

    f.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=Mock(side_effect=entries), async_update_subentry=Mock()
        )
    )
    f.context = {"source": "options"}
    return f


def inputs(**extra):
    return {
        "transport_mode": "vendor_dp",
        "net_key": "12" * 16,
        "app_key": "34" * 16,
        "source_address": 123,
        "iv_index": 0,
        "new_source_confirmed": True,
        "availability_timeout": 300,
        **extra,
    }


@pytest.mark.asyncio
async def test_active_requires_existing_keys_and_new_source_confirmation():
    f = flow()
    result = await f.async_step_init(inputs(net_key="", app_key=""))
    assert result["errors"]["base"] == "keys_required"
    result = await f.async_step_init(inputs(new_source_confirmed=False))
    assert result["errors"]["new_source_confirmed"] == "confirm_unique_source"
    # 176 is the mesh address of a lamp in this network.
    result = await f.async_step_init(inputs(source_address=176))
    assert result["errors"]["source_address"] == "invalid_source"


@pytest.mark.asyncio
async def test_options_profile_has_no_assumed_temperature_range():
    f = flow()
    result = await f.async_step_init(inputs())
    assert result["step_id"] == "profile"
    result = await f.async_step_profile(
        {"ct_range_confirmed": True, "min_kelvin": 0, "max_kelvin": 0}
    )
    assert result["errors"]["base"] == "invalid_ct_range"
    result = await f.async_step_profile({"ct_range_confirmed": False})
    assert result["type"] == "create_entry"
    assert result["data"]["transport_mode"] == "vendor_dp"
    f.hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.asyncio
async def test_lamp_fields_are_not_network_options():
    f = flow()
    f.entry.options = {"device_key": "56" * 16, "node_address": 176}
    await f.async_step_init(inputs())
    result = await f.async_step_profile({})
    assert "device_key" not in result["data"] and "node_address" not in result["data"]


@pytest.mark.asyncio
async def test_a_network_has_one_entry():
    other = SimpleNamespace(entry_id="other", options=inputs(source_address=7), data={})
    f = flow(networks=[other])
    result = await f.async_step_init(inputs())
    assert result["errors"]["net_key"] == "network_in_use"
