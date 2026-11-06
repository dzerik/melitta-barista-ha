"""Persistent recipe selectors resolve independently from HE product numbers."""

import pytest

from tests.test_nivona_units_domains import _client, _writes


@pytest.mark.parametrize("model,selector,field,register", [
    ("660",2,"coffee_amount",10305), ("660",3,"coffee_amount",10405),
    ("660",4,"milk_foam_amount",10608), ("660",5,"water_amount",10706),
    ("040",8,"milk_foam_amount",10912),
    ("756",2,"coffee_amount",10205), ("756",3,"water_amount",10306),
    ("030",9,"milk_foam_amount",10912),
])
async def test_persistent_address_preserves_selected_drink(model,selector,field,register):
    client=_client(model)
    assert await client.write_standard_recipe_param(selector,field,40)
    assert _writes(client)==[(register,40)]


@pytest.mark.parametrize("model,selector",[("030",7),("030",8),("040",7)])
async def test_unresolved_pure_milk_storage_rejects_before_hw(model,selector):
    client=_client(model)
    with pytest.raises(ValueError,match="unsupported"):
        await client.write_standard_recipe_param(selector,"milk_amount",80)
    assert _writes(client)==[]


@pytest.mark.parametrize("model,selector",[("660",6),("795",4),("8103",8),("970",255)])
async def test_unknown_standard_selector_does_not_address_another_register(model,selector):
    client=_client(model)
    with pytest.raises(ValueError,match="unsupported"):
        await client.write_standard_recipe_param(selector,"coffee_amount",40)
    assert _writes(client)==[]
