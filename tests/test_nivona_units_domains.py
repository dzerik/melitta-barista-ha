"""Regressions for Nivona quantities and context-specific numeric controls."""

import math
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.sensor import NivonaMyCoffeeAmountSensor
from tests import MOCK_ADDRESS
from tests.test_nivona_two_cups import _native_controls, _numbers


def _client(model):
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile("nivona"))
    client._capabilities = client.brand.capabilities_for_model(f"{model}000000000000")
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    return client


def _writes(client):
    return [struct.unpack(">hi", call.args[1])
            for call in client._protocol.send_and_wait_ack.await_args_list if call.args[0] == "HW"]


@pytest.mark.parametrize("model,raw", [("960",400),("965",400),("970",400),("920",40),("930",40)])
@pytest.mark.parametrize("context,register", [("temporary",9010),("standard",10009),("mycoffee",20012)])
async def test_40_ml_has_model_correct_wire_units_in_each_context(model, raw, context, register):
    client = _client(model)
    if context == "temporary":
        assert await client.brew_nivona(0, {"coffee_amount":40})
    elif context == "standard":
        assert await client.write_standard_recipe_param(0,"coffee_amount",40)
    else:
        assert await client.write_mycoffee_param(0,"coffee_amount",40)
    assert (register, raw) in _writes(client)


@pytest.mark.parametrize("field,register,value,raw", [
    ("water_amount",9011,500,5000), ("milk_amount",9012,80,800),
    ("milk_foam_amount",9013,100,1000), ("strength",9002,4,4),
    ("coffee_temperature",9006,3,3), ("two_cups",9005,1,1),
])
async def test_quantity_conversion_does_not_scale_choices(field, register, value, raw):
    client = _client("970")
    assert await client.brew_nivona(0,{field:value})
    assert _writes(client) == [(9001,0),(register,raw)]


@pytest.mark.parametrize("field,value", [
    ("strength",5), ("strength",-1), ("coffee_amount",241),
    ("coffee_amount",-1), ("water_amount",501), ("two_cups",2),
    ("milk_amount",241), ("milk_foam_amount",241),
    ("coffee_amount",40.5), ("coffee_amount",math.nan),
    ("coffee_amount",math.inf), ("cofee_amount",40),
])
async def test_invalid_last_override_is_rejected_before_any_write(field, value):
    client = _client("970")
    with pytest.raises(ValueError):
        await client.brew_nivona(0,{"profile":1,field:value})
    assert client._protocol.send_and_wait_ack.await_args_list == []


@pytest.mark.parametrize("model,field,value", [
    ("030","coffee_amount",10), ("030","milk_amount",20),
    ("040","water_amount",20), ("040","milk_amount",10),
])
async def test_remote_minimum_depends_on_model(model, field, value):
    client = _client(model)
    with pytest.raises(ValueError):
        await client.brew_nivona(0,{field:value})
    assert _writes(client) == []


@pytest.mark.parametrize("model", ["030","040"])
async def test_zero_remote_quantities_remain_valid_disabled_components(model):
    client = _client(model)
    assert await client.brew_nivona(0,{"coffee_amount":0,"water_amount":0,"milk_amount":0})
    assert _writes(client) == [(9001,0),(9010,0),(9011,0),(9012,0)]


async def test_unsupported_600_milk_is_not_silently_dropped():
    client = _client("660")
    with pytest.raises(ValueError, match="unsupported"):
        await client.brew_nivona(0,{"coffee_amount":40,"milk_amount":80})
    assert _writes(client) == []


@pytest.mark.parametrize("context", ["standard","mycoffee"])
async def test_persistent_invalid_quantity_is_rejected_without_hw(context):
    client = _client("970")
    method = client.write_standard_recipe_param if context=="standard" else client.write_mycoffee_param
    with pytest.raises(ValueError):
        await method(0,"coffee_amount",-1)
    assert _writes(client) == []


async def test_standard_and_mycoffee_do_not_share_minimum():
    client = _client("970")
    with pytest.raises(ValueError):
        await client.write_standard_recipe_param(0,"coffee_amount",10)
    assert await client.write_mycoffee_param(0,"coffee_amount",10)
    assert _writes(client) == [(20012,100)]


@pytest.mark.parametrize("raw,ml", [(400,40),(405,40.5),(0,0)])
def test_mycoffee_sensor_displays_ml_and_keeps_raw_value(raw, ml):
    client = _client("970")
    client._my_coffee_slots = [{"coffee_amount":raw}]
    sensor = NivonaMyCoffeeAmountSensor(client,MagicMock(),"Nivona",0,"coffee_amount")
    assert sensor.native_value == ml
    assert sensor.native_unit_of_measurement == "mL"
    assert sensor.extra_state_attributes["raw_value"] == raw
    assert client.my_coffee_slots == [{"coffee_amount":raw}]
    assert sensor.unique_id == f"{MOCK_ADDRESS}_mycoffee_slot_0_coffee_amount"


@pytest.mark.parametrize("model,maximum,default", [("756",3,2),("795",5,3),("970",5,3)])
async def test_native_strength_has_model_bounds_and_maps_to_raw(hass, model, maximum, default):
    client = _client(model)
    controls,button = await _native_controls(hass,client)
    strength=controls["strength"]
    assert (strength.native_min_value,strength.native_max_value,strength.native_value)==(1,maximum,default)
    await strength.async_set_native_value(maximum)
    await button.async_press()
    assert (9002,maximum-1) in _writes(client)


async def test_native_invalid_value_does_not_change_state(hass):
    client = _client("756")
    _,numbers = await _numbers(hass,client)
    strength=next(n for n in numbers if getattr(n,"field_name",None)=="strength")
    with pytest.raises(ServiceValidationError):
        await strength.async_set_native_value(4)
    assert strength.native_value==2
    assert not strength.is_user_set


async def test_native_600_ignores_unsupported_untouched_milk(hass):
    client = _client("660")
    controls,button = await _native_controls(hass,client)
    await controls["two_cups"].async_set_native_value(1)
    await button.async_press()
    assert (9005,1) in _writes(client)
    assert all(register!=9008 for register,value in _writes(client))


@pytest.mark.parametrize("restored", ["5","2.5","nan","inf"])
async def test_restored_invalid_override_returns_to_model_default(hass, restored):
    from homeassistant.core import State
    from custom_components.melitta_barista.number import NivonaBrewOverrideNumber
    client=_client("756")
    entry,numbers=await _numbers(hass,client)
    strength=next(n for n in numbers if getattr(n,"field_name",None)=="strength")
    strength.hass=hass
    strength.entity_id="number.strength"
    last=State("number.strength",restored,{"user_set":True})
    with patch.object(NivonaBrewOverrideNumber,"async_get_last_state",AsyncMock(return_value=last)):
        await strength.async_added_to_hass()
    assert strength.native_value==2
    assert not strength.is_user_set


@pytest.mark.parametrize("invalid", ["40.5","nan","inf"])
async def test_forged_fractional_or_nonfinite_native_state_cannot_be_written(hass,invalid):
    client=_client("970")
    controls,button=await _native_controls(hass,client)
    hass.states.async_set(controls["coffee_amount"].entity_id,invalid,{"user_set":True})
    with pytest.raises(ServiceValidationError):
        await button.async_press()
    assert _writes(client)==[]


async def test_600_milk_control_is_unavailable_but_keeps_unique_id(hass):
    client=_client("660")
    entry,numbers=await _numbers(hass,client)
    milk=next(n for n in numbers if getattr(n,"field_name",None)=="milk_amount")
    assert milk.unique_id==f"{MOCK_ADDRESS}_brew_milk_amount"
    assert not milk.available


@pytest.mark.parametrize("context", ["temporary","standard","mycoffee"])
async def test_out_of_domain_choice_is_rejected_in_all_write_contexts(context):
    client=_client("970")
    with pytest.raises(ValueError):
        if context=="temporary":
            await client.brew_nivona(0,{"strength":5})
        else:
            method=client.write_standard_recipe_param if context=="standard" else client.write_mycoffee_param
            await method(0,"strength",5)
    assert _writes(client)==[]


@pytest.mark.parametrize("service,key", [("nivona_write_recipe_param","selector"),("nivona_write_mycoffee_param","slot")])
async def test_persistent_service_explains_invalid_input_as_validation_error(hass, service, key):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.melitta_barista import _async_register_services
    from custom_components.melitta_barista.const import DOMAIN
    client=_client("970")
    entry=MockConfigEntry(domain=DOMAIN,data={"address":MOCK_ADDRESS})
    entry.add_to_hass(hass)
    entry.runtime_data=client
    registry=er.async_get(hass)
    entity=registry.async_get_or_create("button",DOMAIN,f"{MOCK_ADDRESS}_brew",config_entry=entry)
    _async_register_services(hass)
    with pytest.raises(ServiceValidationError,match="coffee_amount"):
        await hass.services.async_call(DOMAIN,service,{
            "entity_id":entity.entity_id,key:0,"param_key":"coffee_amount","value":-1,
        },blocking=True)
    assert _writes(client)==[]


@pytest.mark.parametrize("model",["756","795","8103"])
@pytest.mark.parametrize("value",[-1,500])
@pytest.mark.parametrize("context",["standard","mycoffee"])
async def test_single_temperature_choice_is_checked_in_persistent_context(model,value,context):
    client=_client(model)
    method=client.write_standard_recipe_param if context=="standard" else client.write_mycoffee_param
    with pytest.raises(ValueError):
        await method(0,"temperature",value)
    assert _writes(client)==[]


@pytest.mark.parametrize("field,value,register",[("milk_temperature",3,20010),("milk_foam_temperature",1,20011),("two_cups",3,20007)])
async def test_1030_mycoffee_choices_remain_wider_than_remote(field,value,register):
    client=_client("030")
    assert await client.write_mycoffee_param(0,field,value)
    assert _writes(client)==[(register,value)]
    with pytest.raises(ValueError):
        await client.brew_nivona(0,{field:value})


async def test_970_overall_temperature_does_not_borrow_remote_choices_for_mycoffee():
    client=_client("970")
    with pytest.raises(ValueError):
        await client.write_mycoffee_param(0,"overall_temperature",4)
    assert _writes(client)==[]
    assert await client.brew_nivona(0,{"overall_temperature":4})
    assert _writes(client)==[(9001,0),(9014,4)]


@pytest.mark.parametrize("model,field,value",[("756","enabled",-1),("970","enabled",3),("030","icon",0),("030","icon",10)])
async def test_mycoffee_specific_choices_are_checked(model,field,value):
    client=_client(model)
    with pytest.raises(ValueError):
        await client.write_mycoffee_param(0,field,value)
    assert _writes(client)==[]


@pytest.mark.parametrize("family,model",[("900-light","970"),("700","768")])
async def test_native_strength_refines_after_model_identification(hass,family,model):
    client=_client(model)
    client._capabilities=client.brand.capabilities_for(family)
    entry,numbers=await _numbers(hass,client)
    strength=next(n for n in numbers if getattr(n,"field_name",None)=="strength")
    assert strength.native_max_value==3
    client._capabilities=client.brand.capabilities_for_model(f"{model}000000000000")
    assert strength.native_max_value==5
    assert strength.native_value==3
    strength.hass=hass
    strength.entity_id="number.strength"
    await strength.async_set_native_value(5)
    assert strength.native_value==5


@pytest.mark.parametrize("selector",[-1,256,0.5])
async def test_invalid_selector_is_rejected_before_override_writes(selector):
    client=_client("970")
    with pytest.raises(ValueError):
        await client.brew_nivona(selector,{"coffee_amount":40})
    assert _writes(client)==[]


@pytest.mark.parametrize("selector,field,value",[(8,"coffee_amount",10),(9,"coffee_amount",10),(10,"water_amount",10)])
async def test_chilled_standard_quantities_use_their_own_register_domains(selector,field,value):
    client=_client("8107")
    with pytest.raises(ValueError):
        await client.write_standard_recipe_param(selector,field,value)
    assert _writes(client)==[]


@pytest.mark.parametrize("context",["standard","mycoffee"])
async def test_chilled_raw_marker_remains_a_raw_choice(context):
    client=_client("8107")
    method=client.write_standard_recipe_param if context=="standard" else client.write_mycoffee_param
    assert await method(8 if context=="standard" else 0,"temperature",99)
    assert _writes(client)==[(10803 if context=="standard" else 20006,99)]


@pytest.mark.parametrize("field,restored,expected,user_set",[("coffee_amount","100",100,True),("strength","5",5,True),("coffee_amount","1000",40,False)])
async def test_restore_waits_for_capability_resolution(hass,field,restored,expected,user_set):
    from homeassistant.core import State
    from custom_components.melitta_barista.number import NivonaBrewOverrideNumber
    client=_client("970")
    fallback=client.brand.capabilities_for("900-light")
    client._capabilities=None
    with patch("custom_components.melitta_barista.number.resolve_caps_from_scanner",return_value=fallback):
        entry,numbers=await _numbers(hass,client)
    control=next(n for n in numbers if getattr(n,"field_name",None)==field)
    control.hass=hass
    control.entity_id=f"number.{field}"
    last=State(control.entity_id,restored,{"user_set":True})
    with patch.object(NivonaBrewOverrideNumber,"async_get_last_state",AsyncMock(return_value=last)):
        await control.async_added_to_hass()
    assert control.is_user_set
    # A failed connect must not discard the pending restored input either.
    control._on_connection_change(False)
    assert control.is_user_set
    client._capabilities=client.brand.capabilities_for_model("970000000000000")
    control._on_connection_change(True)
    assert control.native_value==expected
    assert control.is_user_set is user_set
