"""MyCoffee fluid conversions must never affect dimensionless fields."""

from types import SimpleNamespace
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent

from custom_components.melitta_barista.brands import NivonaProfile
from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.sensor import NivonaMyCoffeeAmountSensor

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


@pytest.mark.parametrize("family", ["9101", "900-light", "79x"])
@pytest.mark.parametrize("field,raw,expected_unit", [
    ("enabled", 1, None), ("strength", 4, None),
    ("temperature", 2, None), ("aroma", 3, None),
    ("coffee_amount", 400, "mL"), ("water_amount", 500, "mL"),
    ("milk_amount", 600, "mL"), ("milk_foam_amount", 700, "mL"),
])
def test_mycoffee_units_and_scaling_apply_only_to_fluid_fields(family, field, raw, expected_unit):
    """Strength/enabled retain their exact values and never acquire mL units."""
    brand = NivonaProfile()
    client = SimpleNamespace(
        address=MOCK_ADDRESS, brand=brand, capabilities=brand.capabilities_for(family),
        connected=True, my_coffee_slots=[{field: raw}],
    )
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    entity = NivonaMyCoffeeAmountSensor(client, entry, "Coffee", 0, field)
    assert entity.native_unit_of_measurement == expected_unit
    expected = raw / 10 if family == "900-light" and expected_unit else raw
    assert entity.native_value == expected
    assert entity.extra_state_attributes == {"raw_value": raw}
    assert client.my_coffee_slots == [{field: raw}]


@pytest.mark.parametrize("field,raw", [("enabled", 1), ("strength", 4)])
async def test_existing_mycoffee_entity_discards_old_fluid_unit(hass, field, raw):
    """A released entity with an mL registry value publishes a unitless state."""
    brand = NivonaProfile()
    client = SimpleNamespace(
        address=MOCK_ADDRESS, brand=brand, capabilities=brand.capabilities_for("9101"),
        connected=True, my_coffee_slots=[{field: raw}], model_name="NIVO 9101",
        firmware_version="11090022A22",
        add_status_callback=lambda callback: None, remove_status_callback=lambda callback: None,
        add_connection_callback=lambda callback: None, remove_connection_callback=lambda callback: None,
        add_mycoffee_callback=lambda callback: None, remove_mycoffee_callback=lambda callback: None,
    )
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    entity = NivonaMyCoffeeAmountSensor(client, entry, "Coffee", 0, field)
    entity.entity_id = f"sensor.mycoffee_{field}"
    registry = er.async_get(hass)
    previous = registry.async_get_or_create(
        "sensor", "sensor", entity.unique_id, suggested_object_id=f"mycoffee_{field}",
        unit_of_measurement="mL",
    )
    component = EntityComponent(logging.getLogger(__name__), "sensor", hass)
    await component.async_add_entities([entity])
    state = hass.states.get(previous.entity_id)
    assert state is not None
    assert state.state == str(raw)
    assert "unit_of_measurement" not in state.attributes
    assert registry.async_get(previous.entity_id).unique_id == entity.unique_id
