"""Configured drink names are additive data, not DirectKey slot identities."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN, DirectKeyCategory, RECIPE_NAMES
from custom_components.melitta_barista.protocol import MachineRecipe
from custom_components.melitta_barista.select import MelittaProfileSelect
from custom_components.melitta_barista.ui_contract import MELITTA_RECIPE_NAME_KEYS
from tests import MOCK_CONFIG_DATA
from tests.test_panel_recipes_ws import TestRecipesListDirectKeySection as RecipesListCaller


def _client():
    client = MagicMock()
    client.machine_type = None
    client.active_profile = 1
    client.base_recipes = {202: MachineRecipe(recipe_id=202, recipe_type=2)}
    client._profile_names = {1: 'Alice', 2: 'Bob'}
    client._directkey_recipes = {
        1: {int(DirectKeyCategory.ESPRESSO): MachineRecipe(recipe_id=312, recipe_type=2)},
        2: {int(DirectKeyCategory.ESPRESSO): MachineRecipe(recipe_id=322, recipe_type=1)},
    }
    client.directkey_recipes = client._directkey_recipes
    client.read_recipe = AsyncMock()
    client.read_profile_recipe = AsyncMock()
    return client


@pytest.mark.parametrize('recipe_type', range(24))
def test_known_type_uses_canonical_display_identity(recipe_type):
    from custom_components.melitta_barista.ui_contract import configured_recipe_identity

    assert configured_recipe_identity(recipe_type) == {
        'type': recipe_type,
        'name': RECIPE_NAMES[200 + recipe_type],
        'name_key': MELITTA_RECIPE_NAME_KEYS[200 + recipe_type],
    }


@pytest.mark.parametrize('recipe_type', [None, True, False, '2', 2.0, -1, 24, 25, 255])
def test_unknown_or_custom_type_does_not_invent_a_drink(recipe_type):
    from custom_components.melitta_barista.ui_contract import configured_recipe_identity

    assert configured_recipe_identity(recipe_type) is None


def test_ws_identity_is_per_profile_and_preserves_all_legacy_fields():
    client = _client()
    payload = RecipesListCaller()._call(client)
    rows = [p['recipes'][0] for p in payload['directkey']]
    assert [r['configured_recipe']['name'] for r in rows] == ['Lungo', 'Ristretto']
    assert [(r['id'], r['name'], r['category'], r['type']) for r in rows] == [
        (312, 'Espresso', 'espresso', 2), (322, 'Espresso', 'espresso', 1),
    ]
    assert 'configured_recipe' not in payload['base_recipes'][0]
    client.read_recipe.assert_not_awaited()
    client.read_profile_recipe.assert_not_awaited()


def test_entity_metadata_is_separate_from_frozen_recipe_attribute():
    client = _client()
    entity = MelittaProfileSelect(client, MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA), 'Machine')
    attrs = entity.extra_state_attributes
    assert attrs['directkey_drinks'] == {
        1: {'espresso': {'type': 2, 'name': 'Lungo', 'name_key': 'lungo'}},
        2: {'espresso': {'type': 1, 'name': 'Ristretto', 'name_key': 'ristretto'}},
    }
    legacy = attrs['directkey_recipes'][1]['Espresso']
    assert 'configured_recipe' not in legacy
    assert 'recipe_type' not in legacy
    assert 'name_key' not in legacy
    assert attrs['active_profile'] == 1
    assert 'directkey_drinks' in entity._unrecorded_attributes
    client.read_recipe.assert_not_awaited()
    client.read_profile_recipe.assert_not_awaited()


def test_metadata_refresh_and_removal_do_not_reuse_old_names():
    client = _client()
    entity = MelittaProfileSelect(client, MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA), 'Machine')
    assert entity.extra_state_attributes['directkey_drinks'][1]['espresso']['name'] == 'Lungo'
    client._directkey_recipes[1][0].recipe_type = 3
    assert entity.extra_state_attributes['directkey_drinks'][1]['espresso']['name'] == 'Espresso Doppio'
    client._directkey_recipes[1][0].recipe_type = 24
    assert entity.extra_state_attributes['directkey_drinks'][1]['espresso'] is None
    assert RecipesListCaller()._call(client)['directkey'][0]['recipes'][0]['configured_recipe'] is None
    client.directkey_recipes = {}
    assert entity.extra_state_attributes['directkey_drinks'] == {}
