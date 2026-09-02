"""Tests for the client-side base-recipe cache (UI Contract v1, Zone I-A0).

Covers:
- cache fill + generation bump on base-recipe reads (`read_recipe`),
- non-base recipe ids (DirectKey / temp) staying out of the cache,
- the post-HD refresh path (`reset_recipe_default`) keeping the cache
  current before entity subscribers run,
- `select.py` deriving its legacy display attributes from the cache with
  byte-identical output,
- `panel_api._ws_recipes_list` serving `base_recipes` from the cache.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.const import (
    DOMAIN,
    RECIPE_NAMES,
    RecipeId,
    get_directkey_id,
    DirectKeyCategory,
    TEMP_RECIPE_ID,
)
from custom_components.melitta_barista.protocol import (
    MachineRecipe,
    RecipeComponent,
)


def _espresso_recipe() -> MachineRecipe:
    """Raw espresso recipe: coffee 40 ml strong/one shot, empty component 2."""
    return MachineRecipe(
        recipe_id=int(RecipeId.ESPRESSO),
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


def _connected_client() -> MelittaBleClient:
    """Real MelittaBleClient wired as connected with a mocked protocol."""
    client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
    client._connected = True
    client._client = MagicMock(is_connected=True)
    return client


# ── Client cache ─────────────────────────────────────────────────────────


class TestClientBaseRecipeCache:
    """The raw base-recipe cache on the BLE client."""

    def test_cache_empty_on_init(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert client.base_recipes == {}
        assert client.recipe_cache_generation == 0

    @pytest.mark.asyncio
    async def test_read_recipe_fills_cache(self):
        client = _connected_client()
        recipe = _espresso_recipe()
        client._protocol.read_recipe = AsyncMock(return_value=recipe)

        result = await client.read_recipe(int(RecipeId.ESPRESSO))

        assert result is recipe
        assert client.base_recipes[int(RecipeId.ESPRESSO)] is recipe
        assert client.recipe_cache_generation == 1

    @pytest.mark.asyncio
    async def test_cache_keys_are_plain_ints(self):
        """Reads with a RecipeId enum land under a plain int key."""
        client = _connected_client()
        client._protocol.read_recipe = AsyncMock(return_value=_espresso_recipe())

        await client.read_recipe(RecipeId.ESPRESSO)

        keys = list(client.base_recipes.keys())
        assert keys == [200]
        assert type(keys[0]) is int

    @pytest.mark.asyncio
    async def test_refill_bumps_generation(self):
        client = _connected_client()
        first = _espresso_recipe()
        second = _espresso_recipe()
        client._protocol.read_recipe = AsyncMock(side_effect=[first, second])

        await client.read_recipe(int(RecipeId.ESPRESSO))
        await client.read_recipe(int(RecipeId.ESPRESSO))

        assert client.base_recipes[int(RecipeId.ESPRESSO)] is second
        assert client.recipe_cache_generation == 2

    @pytest.mark.asyncio
    async def test_non_base_ids_not_cached(self):
        """DirectKey and temp recipe ids never enter the base cache."""
        client = _connected_client()
        client._protocol.read_recipe = AsyncMock(return_value=_espresso_recipe())

        dk_id = get_directkey_id(1, DirectKeyCategory.ESPRESSO)
        await client.read_recipe(dk_id)
        await client.read_recipe(TEMP_RECIPE_ID)

        assert client.base_recipes == {}
        assert client.recipe_cache_generation == 0

    @pytest.mark.asyncio
    async def test_failed_read_not_cached(self):
        client = _connected_client()
        client._protocol.read_recipe = AsyncMock(return_value=None)

        result = await client.read_recipe(int(RecipeId.ESPRESSO))

        assert result is None
        assert client.base_recipes == {}
        assert client.recipe_cache_generation == 0

    @pytest.mark.asyncio
    async def test_disconnected_read_not_cached(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.read_recipe(int(RecipeId.ESPRESSO)) is None
        assert client.base_recipes == {}
        assert client.recipe_cache_generation == 0

    @pytest.mark.asyncio
    async def test_refresh_path_updates_cache(self):
        """reset_recipe_default's post-HD re-read lands in the cache."""
        fresh = _espresso_recipe()
        client = _connected_client()
        client._protocol.reset_default = AsyncMock(return_value=True)
        client._protocol.read_recipe = AsyncMock(return_value=fresh)

        assert await client.reset_recipe_default(int(RecipeId.ESPRESSO)) is True

        assert client.base_recipes[int(RecipeId.ESPRESSO)] is fresh
        assert client.recipe_cache_generation == 1

    @pytest.mark.asyncio
    async def test_refresh_subscribers_see_updated_cache(self):
        """Entity refresh callbacks run after the cache is already current."""
        fresh = _espresso_recipe()
        client = _connected_client()
        client._protocol.reset_default = AsyncMock(return_value=True)
        client._protocol.read_recipe = AsyncMock(return_value=fresh)

        seen: list[MachineRecipe | None] = []
        client.add_recipe_refresh_callback(
            lambda rid, recipe: seen.append(client.base_recipes.get(rid))
        )

        await client.reset_recipe_default(int(RecipeId.ESPRESSO))

        assert seen == [fresh]

    def test_store_base_recipe_direct(self):
        """store_base_recipe fills the cache and bumps the generation."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        recipe = _espresso_recipe()

        client.store_base_recipe(RecipeId.ESPRESSO, recipe)

        assert client.base_recipes == {200: recipe}
        assert client.recipe_cache_generation == 1


# ── select.py consumes the cache ─────────────────────────────────────────


_ESPRESSO_LEGACY_ATTRS = {
    "c1_process": "coffee",
    "c1_intensity": "strong",
    "c1_aroma": "standard",
    "c1_temperature": "normal",
    "c1_shots": "one",
    "c1_portion_ml": 40,
    "c2_process": "none",
    "c2_intensity": "medium",
    "c2_aroma": "standard",
    "c2_temperature": "normal",
    "c2_shots": "none",
    "c2_portion_ml": 0,
}


def _assert_legacy_attrs(attrs) -> None:
    """Assert the legacy c1_/c2_ display keys survive byte-identical.

    Zone I-C additively extends the derived dicts (per-component
    ``blend`` tokens and the structured ``icon`` IconSpec inside the
    recorder-excluded ``recipes`` attribute), so legacy parity is a
    superset check on the frozen legacy keys/values.
    """
    for key, value in _ESPRESSO_LEGACY_ATTRS.items():
        assert attrs[key] == value


def _select_entity():
    """MelittaRecipeSelect wired to a mock client with a real cache dict."""
    from custom_components.melitta_barista.select import MelittaRecipeSelect

    client = MagicMock()
    client.address = "AA:BB:CC:DD:EE:FF"
    client.connected = True
    client.machine_type = None
    client.base_recipes = {}
    client.recipe_cache_generation = 0

    async def _read(recipe_id: int):
        if recipe_id == int(RecipeId.ESPRESSO):
            recipe = _espresso_recipe()
            client.base_recipes[int(recipe_id)] = recipe
            client.recipe_cache_generation += 1
            return recipe
        return None

    client.read_recipe = AsyncMock(side_effect=_read)

    entity = MelittaRecipeSelect(client, MagicMock(), "Test Machine")
    entity.async_write_ha_state = MagicMock()
    return entity, client


class TestSelectConsumesCache:
    """select.py derives display attrs from the client cache (parity)."""

    @pytest.mark.asyncio
    async def test_preload_derives_attrs_from_cache(self):
        entity, client = _select_entity()

        await entity._preload_recipes()

        _assert_legacy_attrs(entity._all_recipes["Espresso"])
        # Only the espresso read succeeded → only espresso attrs derived.
        assert list(entity._all_recipes.keys()) == ["Espresso"]
        entity.async_write_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_preload_uses_client_cache_as_source(self):
        """Recipes already in the cache surface even if this read fails."""
        entity, client = _select_entity()
        # Pre-populated by a previous session/read.
        cached = _espresso_recipe()
        client.base_recipes[int(RecipeId.CAPPUCCINO)] = cached

        await entity._preload_recipes()

        _assert_legacy_attrs(entity._all_recipes["Cappuccino"])

    def test_on_recipe_refresh_reads_from_cache(self):
        """_on_recipe_refresh derives attrs from the client cache."""
        entity, client = _select_entity()
        fresh = _espresso_recipe()
        client.base_recipes[int(RecipeId.ESPRESSO)] = fresh

        entity._on_recipe_refresh(int(RecipeId.ESPRESSO), fresh)

        _assert_legacy_attrs(entity._all_recipes["Espresso"])
        entity.async_write_ha_state.assert_called_once()

    def test_on_recipe_refresh_unknown_id_ignored(self):
        entity, client = _select_entity()
        entity._on_recipe_refresh(999, _espresso_recipe())
        assert entity._all_recipes == {}
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_select_option_on_demand_read_uses_cache(self):
        entity, client = _select_entity()

        await entity.async_select_option("Espresso")

        _assert_legacy_attrs(entity._all_recipes["Espresso"])

    @pytest.mark.asyncio
    async def test_extra_state_attributes_parity(self):
        """Selected recipe attrs flattened + full `recipes` table exposed."""
        entity, client = _select_entity()
        await entity.async_select_option("Espresso")

        attrs = entity.extra_state_attributes

        _assert_legacy_attrs(attrs)
        assert list(attrs["recipes"].keys()) == ["Espresso"]
        _assert_legacy_attrs(attrs["recipes"]["Espresso"])


# ── panel_api._ws_recipes_list serves the cache ──────────────────────────


class TestPanelBaseRecipes:
    """_ws_recipes_list fills base_recipes from client.base_recipes."""

    def _call(self, client):
        from custom_components.melitta_barista.panel_api import _ws_recipes_list

        hass = MagicMock()
        entry = MagicMock()
        entry.domain = DOMAIN
        entry.runtime_data = client
        hass.config_entries.async_get_entry.return_value = entry

        connection = MagicMock()
        _ws_recipes_list(hass, connection, {"id": 5, "entry_id": "entry_1"})
        connection.send_result.assert_called_once()
        msg_id, payload = connection.send_result.call_args.args
        assert msg_id == 5
        return payload

    def test_base_recipes_filled_from_cache(self):
        client = MagicMock()
        client._profile_names = {0: "My Coffee"}
        client._directkey_recipes = {}
        client.base_recipes = {
            int(RecipeId.CAPPUCCINO): _espresso_recipe(),
            int(RecipeId.ESPRESSO): _espresso_recipe(),
        }

        payload = self._call(client)

        base = payload["base_recipes"]
        assert [row["id"] for row in base] == [200, 213]  # sorted by id
        assert [row["name"] for row in base] == [
            RECIPE_NAMES[RecipeId.ESPRESSO],
            RECIPE_NAMES[RecipeId.CAPPUCCINO],
        ]
        c1 = base[0]["components"][0]
        assert c1["process"] == "coffee"
        assert c1["portion_ml"] == 40
        assert c1["intensity"] == "strong"

    def test_base_recipes_carry_icon_spec(self):
        """§2.1: WS recipes/list is an icon surface — every base recipe
        entry carries the same IconSpec build_icon_spec derives from its
        cached components."""
        from custom_components.melitta_barista.ui_contract import (
            build_icon_spec,
            component_to_tokens,
        )

        client = MagicMock()
        client._profile_names = {}
        client._directkey_recipes = {}
        recipe = _espresso_recipe()
        client.base_recipes = {int(RecipeId.ESPRESSO): recipe}

        payload = self._call(client)

        row = payload["base_recipes"][0]
        expected = build_icon_spec([
            component_to_tokens(recipe.component1),
            component_to_tokens(recipe.component2),
        ])
        assert expected is not None  # espresso composition yields a spec
        assert row["icon"] == expected
        assert row["icon"]["spec_version"] == 1
        assert row["icon"]["layers"][0]["role"] == "coffee"

    def test_directkey_recipes_carry_icon_spec(self):
        """DirectKey rows in recipes/list carry an icon too (`null` when
        the slot has no recipe — client renders its default)."""
        client = MagicMock()
        client._profile_names = {1: "Anna"}
        client._directkey_recipes = {1: {0: _espresso_recipe(), 1: None}}
        client.base_recipes = {}

        payload = self._call(client)

        rows = payload["directkey"][0]["recipes"]
        assert rows[0]["icon"] is not None
        assert rows[0]["icon"]["spec_version"] == 1
        assert rows[1]["icon"] is None

    def test_base_recipes_empty_cache(self):
        client = MagicMock()
        client._profile_names = {}
        client._directkey_recipes = {}
        client.base_recipes = {}

        payload = self._call(client)

        assert payload["base_recipes"] == []
