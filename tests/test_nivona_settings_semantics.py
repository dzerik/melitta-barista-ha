"""Machine settings follow physical models and validated ordinal domains."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.melitta_barista.select import BrandSettingSelect
from tests.test_nivona_units_domains import _client, _writes


@pytest.mark.parametrize("model,ids",[
    ("920",(102,104,109,110,111,112)),("930",(102,104,109,110,111,112)),
    ("960",(102,103,104,105,106,107,108,109,110,111,112)),
    ("965",(102,103,104,105,106,107,108,109,110,111,112)),
    ("970",(102,103,104,105,106,107,108,109,110,111,112)),
])
def test_setting_membership_follows_physical_model(model,ids):
    assert tuple(x.setting_id for x in _client(model).capabilities.settings)==ids


def test_970_colors_and_duration_codes_are_not_shifted():
    settings={x.setting_id:x for x in _client("970").capabilities.settings}
    assert settings[106].options==((0,"red"),(1,"orange"),(2,"yellow"),(3,"green"),
                                   (4,"cyan"),(5,"blue"),(6,"magenta"),(7,"white"),(8,"rainbow"))
    durations=dict(settings[109].options)
    assert durations[9]=="14 h"
    assert durations[10]=="16 h"
    assert "off" not in durations.values()


@pytest.mark.parametrize("model,setting,value",[
    ("930",105,1),("930",106,0),("970",106,9),("970",111,24),("970",112,60),
    ("970",109,11),("8103",105,1),("8103",104,16),("040",109,16),
    ("795",104,9),("756",104,10),
])
async def test_unsupported_or_ambiguous_setting_is_rejected_before_hw(model,setting,value):
    client=_client(model)
    with pytest.raises(ValueError):
        await client.write_setting(setting,value)
    assert _writes(client)==[]


@pytest.mark.parametrize("model,setting,value",[("970",106,0),("970",106,7),("970",109,10),("930",109,10),("040",109,9),("030",109,9)])
async def test_verified_settings_preserve_raw_codes(model,setting,value):
    client=_client(model)
    assert await client.write_setting(setting,value)
    assert _writes(client)==[(setting,value)]


async def test_ambiguous_setting_is_unavailable_and_not_read():
    client=_client("8103")
    client._protocol.read_numerical=AsyncMock(return_value=1)
    descriptor=next(x for x in client.capabilities.settings if x.setting_id==105)
    entity=BrandSettingSelect(client,MagicMock(),"Nivona",descriptor)
    assert not entity.available
    assert await client.read_setting(105) is None
    client._protocol.read_numerical.assert_not_awaited()


@pytest.mark.parametrize("model",["756","795"])
def test_conflicting_auto_off_value_is_not_off_or_fourteen_hours(model):
    descriptor=next(x for x in _client(model).capabilities.settings if x.key=="auto_off")
    assert set(dict(descriptor.options))==set(range(9))


async def test_unsupported_stale_select_is_disabled_without_losing_customization(hass):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.melitta_barista.const import DOMAIN
    from custom_components.melitta_barista.select import async_setup_entry
    client=_client("930")
    entry=MockConfigEntry(domain=DOMAIN,data={"address":client.address})
    entry.add_to_hass(hass)
    entry.runtime_data=client
    registry=er.async_get(hass)
    old=registry.async_get_or_create("select",DOMAIN,f"{client.address}_setting_tank_light",config_entry=entry)
    registry.async_update_entity(old.entity_id,name="My custom lamp")
    await async_setup_entry(hass,entry,lambda entities:None)
    kept=registry.async_get(old.entity_id)
    assert kept is not None
    assert kept.name=="My custom lamp"
    assert kept.disabled_by==er.RegistryEntryDisabler.INTEGRATION


def test_auto_on_label_preserves_existing_object_id_and_raw_option():
    client = _client("930")
    descriptor = next(x for x in client.capabilities.settings if x.setting_id == 110)
    entity = BrandSettingSelect(client, MagicMock(), "Nivona", descriptor)
    assert entity.name == "Automatic switch-on"
    assert entity.suggested_object_id == "Auto-on deactivated"
    assert entity.unique_id == f"{client.address}_setting_auto_on_deactivated"
    assert entity._label_to_code["on"] == 1
