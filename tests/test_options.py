from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from custom_components.flinq_orion.config_flow import OptionsFlow


class Flow(OptionsFlow):
    entry = SimpleNamespace(options={}, entry_id="target", data={"address": "DC:23:4F:6E:A0:12"})

    @property
    def config_entry(self):
        return self.entry


def flow():
    f = Flow()
    f.hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=Mock(return_value=[])))
    f.context = {"source": "options"}
    return f


def inputs(**extra):
    return {
        "transport_mode": "vendor_dp",
        "net_key": "12" * 16,
        "app_key": "34" * 16,
        "device_key": "",
        "source_address": 123,
        "node_address": 176,
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


@pytest.mark.asyncio
async def test_controller_address_cannot_be_shared_by_entries():
    f = flow()
    f.hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="other", options=inputs(), domain="flinq_orion", data={})
    ]
    result = await f.async_step_init(inputs())
    assert result["errors"]["source_address"] == "source_in_use"
