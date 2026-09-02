"""Backend plumbing tests for the panel's DirectKey recipe editor.

The editor rides on surfaces that already existed (`recipes/list` WS,
`save_directkey` / `reset_recipe` services); this file pins the two
pieces that were widened for it:

- `RESET_RECIPE_SCHEMA` now accepts per-profile DirectKey ids (302-388)
  next to the base range (200-223), still rejecting the gap between the
  two classes.
- `_store_refreshed_base_recipe` routes a post-HD DirectKey re-read back
  into the per-profile `_directkey_recipes` cache (and notifies profile
  subscribers) so the editor's reset flow observes factory values on the
  very next `recipes/list` fetch.
- The `recipes/list` payload's `directkey` section keyed by profile that
  the editor consumes (profile ids/names + category rows with BLE ids).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.melitta_barista import RESET_RECIPE_SCHEMA
from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.const import (
    DOMAIN,
    DirectKeyCategory,
    RecipeId,
    get_directkey_id,
)
from custom_components.melitta_barista.protocol import (
    MachineRecipe,
    RecipeComponent,
)


def _espresso_recipe(recipe_id: int = int(RecipeId.ESPRESSO)) -> MachineRecipe:
    """Raw espresso recipe: coffee 40 ml strong/one shot, empty component 2."""
    return MachineRecipe(
        recipe_id=recipe_id,
        recipe_type=0,
        component1=RecipeComponent(
            process=1, shots=1, blend=1, intensity=3,
            aroma=0, temperature=1, portion=8,
        ),
        component2=RecipeComponent(
            process=0, shots=0, blend=1, intensity=2,
            aroma=0, temperature=1, portion=0,
        ),
    )


# ── RESET_RECIPE_SCHEMA id ranges ────────────────────────────────────────


class TestResetRecipeSchema:
    """recipe_id accepts base AND DirectKey ids, nothing in between."""

    @pytest.mark.parametrize("recipe_id", [200, 223, 302, 388])
    def test_valid_ids_accepted(self, recipe_id):
        data = RESET_RECIPE_SCHEMA(
            {"entity_id": "button.machine_reset", "recipe_id": recipe_id}
        )
        assert data["recipe_id"] == recipe_id

    def test_directkey_id_math_covered_by_range(self):
        """Every reachable DirectKey id (profile 0-8, cat 0-6) validates."""
        for profile_id in range(9):
            for category in DirectKeyCategory:
                dk_id = get_directkey_id(profile_id, category)
                data = RESET_RECIPE_SCHEMA(
                    {"entity_id": "button.machine_reset", "recipe_id": dk_id}
                )
                assert data["recipe_id"] == dk_id

    @pytest.mark.parametrize("recipe_id", [199, 224, 250, 301, 389])
    def test_invalid_ids_rejected(self, recipe_id):
        with pytest.raises(vol.Invalid):
            RESET_RECIPE_SCHEMA(
                {"entity_id": "button.machine_reset", "recipe_id": recipe_id}
            )

    def test_recipe_id_stays_optional(self):
        """Omitting recipe_id (reset the selected recipe) still validates."""
        data = RESET_RECIPE_SCHEMA({"entity_id": "button.machine_reset"})
        assert "recipe_id" not in data


# ── post-HD refresh routing into the DirectKey cache ─────────────────────


class TestDirectKeyRefreshRouting:
    """_store_refreshed_base_recipe keeps _directkey_recipes current."""

    def _client(self) -> MelittaBleClient:
        return MelittaBleClient("AA:BB:CC:DD:EE:FF")

    def test_directkey_id_lands_in_profile_cache(self):
        client = self._client()
        dk_id = get_directkey_id(2, DirectKeyCategory.CAPPUCCINO)
        recipe = _espresso_recipe(dk_id)

        client._store_refreshed_base_recipe(dk_id, recipe)

        assert client._directkey_recipes[2][DirectKeyCategory.CAPPUCCINO] is recipe
        assert dk_id not in client.base_recipes

    def test_directkey_refresh_notifies_profile_subscribers(self):
        client = self._client()
        seen = []
        client._profile_callbacks.append(lambda: seen.append(True))

        client._store_refreshed_base_recipe(
            get_directkey_id(0, DirectKeyCategory.ESPRESSO),
            _espresso_recipe(),
        )

        assert seen == [True]

    def test_base_id_still_fills_base_cache_only(self):
        client = self._client()
        recipe = _espresso_recipe()

        client._store_refreshed_base_recipe(int(RecipeId.ESPRESSO), recipe)

        assert client.base_recipes[int(RecipeId.ESPRESSO)] is recipe
        assert client._directkey_recipes == {}

    @pytest.mark.parametrize("recipe_id", [250, 301, 309, 399])
    def test_out_of_layout_ids_ignored(self, recipe_id):
        """Ids in the base-DirectKey gap or off the slot layout are dropped.

        309 = profile 0, category 7 — a category byte DirectKeyCategory
        does not define; 399 would be profile 9 (beyond the 0-8 range).
        """
        client = self._client()

        client._store_refreshed_base_recipe(recipe_id, _espresso_recipe(recipe_id))

        assert client._directkey_recipes == {}
        assert recipe_id not in client.base_recipes


# ── recipes/list directkey section (editor's data source) ────────────────


class TestRecipesListDirectKeySection:
    """The per-profile table the editor consumes, as served over WS."""

    def _call(self, client):
        from custom_components.melitta_barista.panel_api import _ws_recipes_list

        hass = MagicMock()
        entry = MagicMock()
        entry.domain = DOMAIN
        entry.runtime_data = client
        hass.config_entries.async_get_entry.return_value = entry

        connection = MagicMock()
        _ws_recipes_list(hass, connection, {"id": 7, "entry_id": "entry_1"})
        connection.send_result.assert_called_once()
        _msg_id, payload = connection.send_result.call_args.args
        return payload

    def test_profiles_carry_names_and_ble_ids(self):
        client = MagicMock()
        client.base_recipes = {}
        client._profile_names = {0: "My Coffee", 3: "Anna"}
        client._directkey_recipes = {
            0: {int(DirectKeyCategory.ESPRESSO): _espresso_recipe()},
            3: {int(DirectKeyCategory.WATER): _espresso_recipe()},
            5: {int(DirectKeyCategory.MILK): _espresso_recipe()},
        }

        payload = self._call(client)

        directkey = payload["directkey"]
        assert [p["profile_id"] for p in directkey] == [0, 3, 5]
        assert directkey[0]["profile_name"] == "My Coffee"
        assert directkey[1]["profile_name"] == "Anna"
        # Unnamed profile falls back to "Profile N" (editor shows it as-is).
        assert directkey[2]["profile_name"] == "Profile 5"
        # Rows carry the real BLE slot id (302 + profile*10 + category).
        assert directkey[0]["recipes"][0]["id"] == get_directkey_id(
            0, DirectKeyCategory.ESPRESSO
        )
        assert directkey[1]["recipes"][0]["id"] == get_directkey_id(
            3, DirectKeyCategory.WATER
        )

    def test_rows_carry_components_and_icon(self):
        client = MagicMock()
        client.base_recipes = {}
        client._profile_names = {}
        client._directkey_recipes = {
            1: {int(DirectKeyCategory.CAPPUCCINO): _espresso_recipe()},
        }

        payload = self._call(client)

        row = payload["directkey"][0]["recipes"][0]
        assert row["name"] == "Cappuccino"
        assert row["components"][0]["process"] == "coffee"
        assert row["components"][0]["portion_ml"] == 40
        assert row["icon"] is not None and row["icon"]["spec_version"] == 1
