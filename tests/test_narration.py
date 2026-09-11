"""Tests for the server-side narration renderer (`CC/narration.py`, spec §4).

Three things are being pinned here:

1. **The sentences themselves.** The maintainer's target sentence renders
   byte-exact in Russian, and the grammar bar (`ru`/`de`/`tr`) survives a
   payload that exercises every slot.
2. **The failure modes.** They are the reason design C was chosen: an unknown
   token drops one clause, a missing key renders the *whole* sentence in
   English rather than code-switching, free text is sanitised, the clause and
   volume budgets hold, and `render()` never raises whatever it is fed.
3. **The 29-locale property test** — every shipped locale file, over a
   deliberately capped slot matrix, produces a clean sentence.

The renderer is pure, so everything here runs on plain dicts and the real asset
files; no `hass` fixture is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.melitta_barista import narration
from custom_components.melitta_barista.const import DirectKeyCategory
from custom_components.melitta_barista.narration import (
    DIRECTKEY_NAME_KEYS,
    NARRATION_DRINK_PREFIX,
    NARRATION_MAX_CLAUSES,
    NARRATION_MAX_FREE_TEXT,
    NARRATION_MAX_LABEL,
    NARRATION_MAX_ML,
    NARRATION_OMITTED_TOKENS,
    NARRATION_PLACEHOLDERS,
    NARRATION_SEPARATOR,
    SLOT_ORDER,
    Narration,
    _speakable,
    load_narration_strings,
    narration_keys,
    render,
    substitute,
)

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "melitta_barista"
NARRATION_DIR = COMPONENT_DIR / "narration_strings"
UI_STRINGS_DIR = COMPONENT_DIR / "ui_strings"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _narration(locale: str) -> dict[str, str]:
    return _load(NARRATION_DIR / f"{locale}.json")


def _ui(locale: str) -> dict[str, str]:
    """The en-overlaid merged ui_strings map, exactly as the WS loader builds it."""
    en = _load(UI_STRINGS_DIR / "en.json")
    if locale == "en":
        return en
    return {**en, **_load(UI_STRINGS_DIR / f"{locale}.json")}


def _render(payload, event_type, locale="en", *, narration_map=None, ui_map=None):
    """Render with the real English assets and, by default, real locale assets."""
    return render(
        payload,
        event_type=event_type,
        locale=locale,
        narration=_narration(locale) if narration_map is None else narration_map,
        narration_en=_narration("en"),
        ui_strings=_ui(locale) if ui_map is None else ui_map,
        ui_strings_en=_ui("en"),
    )


# The maintainer's reference brew: a DirectKey cappuccino on profile 2, strong,
# 100 ml of coffee and 150 ml of hot milk.
CAPPUCCINO = {
    "source": "ha",
    "recipe_source": "directkey",
    "recipe_key": "cappuccino",
    "recipe_name": "Cappuccino",
    "profile": 2,
    "profile_name": "Anna",
    "two_cups": False,
    "components": [
        {"process": "coffee", "intensity": "strong", "aroma": "standard",
         "temperature": "normal", "shots": "none", "portion_ml": 100},
        {"process": "milk", "intensity": "medium", "aroma": "standard",
         "temperature": "normal", "shots": "none", "portion_ml": 150},
    ],
    "total_ml": 250,
    "duration_s": 42,
    "cancel_detection": True,
    "final": True,
}


# ---------------------------------------------------------------------------
# The authored sentences
# ---------------------------------------------------------------------------

def test_english_target_sentence():
    """The reference brew, in the normative English baseline."""
    result = _render(CAPPUCCINO, "brew_finished")
    assert result.text == (
        "Ready: Cappuccino — 100 ml of coffee, 150 ml of hot milk, strong, "
        "profile Anna."
    )
    assert result.key == "narration.event.brew_finished.named_detail"
    assert result.language == "en"
    assert result.complete is True
    assert result.missing_keys == ()


def test_russian_target_sentence_byte_exact():
    """§4.4's target sentence, the acceptance criterion for the whole feature.

    The drink name is Cyrillic because ru's `narration_strings/ru.json` carries
    the spoken override `narration.drink.cappuccino` (M18). The served
    `recipes.name.cappuccino` stays the Latin `"Cappuccino"` in ru and is still
    what every picker button shows — do not "fix" either one into the other.
    """
    result = _render(CAPPUCCINO, "brew_finished", "ru")
    assert result.text == (
        "Сварено: капучино — 100 мл кофе, 150 мл горячего молока, "
        "высокой интенсивности, профиль Anna."
    )
    assert result.language == "ru"
    assert result.complete is True


def test_german_clauses_are_the_authored_ones():
    """de renders its own measure phrases and its own intensity clause."""
    text = _render(CAPPUCCINO, "brew_finished", "de").text
    assert "100 ml Kaffee" in text
    assert "150 ml heiße Milch" in text
    assert "starke Intensität" in text
    assert "Profil Anna" in text


def test_turkish_reorders_the_profile_placeholder():
    """tr puts the name first (`Anna profili`) — placeholders may be reordered."""
    text = _render(CAPPUCCINO, "brew_finished", "tr").text
    assert "Anna profili" in text
    assert "100 ml kahve" in text


def test_front_panel_brew_says_only_what_is_known():
    """Nothing was recorded, so the sentence claims nothing (ru)."""
    result = _render({"source": "machine"}, "brew_finished", "ru")
    assert result.text == "Напиток готов."
    assert result.key == "narration.event.brew_finished.unnamed"


def test_every_maintenance_token_has_its_own_russian_sentence():
    """Six distinct agreement forms — the reason the 8 heads are enumerated."""
    strings = _narration("ru")
    tokens = [
        key.rsplit(".", 1)[1] for key in strings
        if key.startswith("narration.event.maintenance_finished.")
    ]
    assert len(tokens) == 8
    sentences = {
        token: _render({"process": token}, "maintenance_finished", "ru").text
        for token in tokens
    }
    assert all(text for text in sentences.values())
    assert len(set(sentences.values())) == 8


def test_brew_started_and_cancelled_name_the_drink_without_the_detail():
    """Only `brew_finished` reads the composition out loud."""
    started = _render(CAPPUCCINO, "brew_started")
    cancelled = _render(CAPPUCCINO, "brew_cancelled")
    assert started.text == "Brewing: Cappuccino."
    assert cancelled.text == "Cancelled: Cappuccino."


# ---------------------------------------------------------------------------
# Clause assembly
# ---------------------------------------------------------------------------

def test_single_component_never_produces_a_doubled_separator():
    payload = {"components": [{"process": "coffee", "portion_ml": 40}]}
    text = _render(payload, "brew_finished").text
    assert text == "Your drink is ready: 40 ml of coffee."
    assert ", ," not in text


def test_empty_clause_list_demotes_the_detail_variant():
    """A named drink with nothing else to say uses `.named`, not `.named_detail`."""
    result = _render({"recipe_key": "espresso"}, "brew_finished")
    assert result.key == "narration.event.brew_finished.named"
    assert result.text == "Ready: Espresso."


def test_blank_drink_demotes_to_the_unnamed_variant():
    """Free text that sanitises to nothing must not leave a dangling colon."""
    payload = {"recipe_source": "freestyle", "recipe_name": "  \U0001f49a  ",
               "components": [{"process": "water", "portion_ml": 200}]}
    result = _render(payload, "brew_finished")
    assert result.key == "narration.event.brew_finished.unnamed_detail"
    assert result.text == "Your drink is ready: 200 ml of hot water."


def test_unknown_component_token_drops_one_clause_only():
    """A firmware that grows a process byte loses a clause, not the sentence."""
    payload = {
        "recipe_key": "espresso",
        "components": [
            {"process": "cocoa", "portion_ml": 50},
            {"process": "coffee", "portion_ml": 30},
        ],
    }
    result = _render(payload, "brew_finished")
    assert result.text == "Ready: Espresso — 30 ml of coffee."
    assert result.complete is False


def test_unknown_value_token_drops_one_clause_only():
    payload = {"recipe_key": "espresso", "components": [
        {"process": "coffee", "portion_ml": 30, "intensity": "nuclear"}]}
    result = _render(payload, "brew_finished")
    assert result.text == "Ready: Espresso — 30 ml of coffee."
    assert result.complete is False


@pytest.mark.parametrize("family,token", sorted(
    (family, token)
    for family, tokens in NARRATION_OMITTED_TOKENS.items()
    for token in tokens
    if family != "process"
))
def test_omitted_tokens_are_never_spoken(family, token):
    """The machine defaults stay silent — speaking them informs nobody."""
    payload = {"recipe_key": "espresso", "components": [
        {"process": "coffee", "portion_ml": 30, family: token}]}
    result = _render(payload, "brew_finished")
    assert result.text == "Ready: Espresso — 30 ml of coffee."
    assert result.complete is True


def test_strength_is_read_from_the_coffee_component_not_the_first_one():
    """A milk-first drink must narrate the coffee's strength, not the milk's.

    `component_to_tokens` fills `intensity` in on every component from the wire
    defaults, so the milk component of a latte macchiato carries a filler
    `"medium"`. Reading the first component's value used to state the exact
    opposite of what the user brewed, with `complete=True` to vouch for it.
    """
    payload = {
        "recipe_key": "latte_macchiato",
        "components": [
            {"process": "milk", "intensity": "medium", "aroma": "standard",
             "temperature": "normal", "shots": "none", "portion_ml": 150},
            {"process": "coffee", "intensity": "very_strong", "aroma": "intense",
             "temperature": "normal", "shots": "two", "portion_ml": 40},
        ],
    }
    result = _render(payload, "brew_finished")
    assert result.text == (
        "Ready: Latte Macchiato — 150 ml of hot milk, 40 ml of coffee, "
        "very strong, intense aroma, two extra shots."
    )
    assert "medium" not in result.text
    assert result.complete is True


def test_a_drink_without_coffee_has_no_strength_clause():
    """Hot water and milk froth have no strength, however the frame is filled in."""
    payload = {"recipe_key": "water",
               "components": [{"process": "water", "intensity": "medium",
                               "aroma": "standard", "shots": "none",
                               "portion_ml": 150}]}
    english = _render(payload, "brew_finished")
    assert english.text == "Ready: Hot Water — 150 ml of hot water."
    assert english.complete is True
    assert _render(payload, "brew_finished", "ru").text == (
        "Сварено: Горячая вода — 150 мл горячей воды."
    )

    froth = _render(
        {"recipe_key": "milk_froth",
         "components": [{"process": "milk", "intensity": "strong",
                         "temperature": "high", "portion_ml": 120}]},
        "brew_finished",
    )
    assert "strong" not in froth.text


def test_milk_temperature_is_still_read_from_the_milk_component():
    """`temperature` is a real per-component fact and stays position-scanned."""
    payload = {"recipe_key": "latte_macchiato", "components": [
        {"process": "milk", "temperature": "high", "portion_ml": 150},
        {"process": "coffee", "temperature": "normal", "portion_ml": 40},
    ]}
    assert "extra hot" in _render(payload, "brew_finished").text


def test_absent_blend_key_produces_no_clause_but_a_present_one_does():
    """Absence is the machine-default signal (§2.4); a token is a real fact."""
    base = {"process": "coffee", "portion_ml": 30}
    without = _render({"components": [base]}, "brew_finished")
    assert "hopper" not in without.text
    assert without.complete is True

    with_blend = _render(
        {"components": [{**base, "blend": "hopper_1"}]}, "brew_finished"
    )
    assert with_blend.text == "Your drink is ready: 30 ml of coffee, from hopper 1."


def test_clause_budget_caps_an_over_specified_payload():
    """Overflow drops the tail of SLOT_ORDER and admits it via `complete`."""
    payload = {
        "recipe_key": "espresso",
        "profile_name": "Anna",
        "two_cups": True,
        "phase_index": 3,
        "phase_total": 4,
        "components": [
            {"process": "coffee", "portion_ml": 100, "intensity": "very_strong",
             "aroma": "intense", "temperature": "high", "shots": "three",
             "blend": "hopper_2"},
            {"process": "milk", "portion_ml": 150},
        ],
    }
    result = _render(payload, "brew_finished")
    assert result.complete is False
    assert result.text.count(NARRATION_SEPARATOR) == NARRATION_MAX_CLAUSES - 1
    # The tail of SLOT_ORDER is what goes: the phase and the components stay.
    assert "pour 4 of 4" in result.text
    assert "profile Anna" not in result.text


def test_phase_clause_only_for_a_genuinely_multi_phase_drink():
    single = _render(
        {"recipe_name": "Flat White", "phase_index": 0, "phase_total": 1,
         "components": [{"process": "coffee", "portion_ml": 30}]},
        "brew_finished",
    )
    assert "pour" not in single.text

    multi = _render(
        {"recipe_name": "Flat White", "phase_index": 1, "phase_total": 2,
         "components": [{"process": "milk", "portion_ml": 120}]},
        "brew_finished",
    )
    assert multi.text == "Ready: Flat White — pour 2 of 2, 120 ml of hot milk."


def test_slot_order_is_the_rendered_order():
    payload = {
        "recipe_key": "espresso",
        "profile_name": "Anna",
        "two_cups": True,
        "components": [{"process": "coffee", "portion_ml": 30,
                        "intensity": "mild", "blend": "hopper_2"}],
    }
    text = _render(payload, "brew_finished").text
    positions = [
        text.index(fragment)
        for fragment in ("30 ml of coffee", "mild", "from hopper 2",
                         "two cups", "profile Anna")
    ]
    assert positions == sorted(positions)
    assert SLOT_ORDER.index("component1") < SLOT_ORDER.index("profile")


# ---------------------------------------------------------------------------
# Volumes and free text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("millilitres", [None, 0, -5, NARRATION_MAX_ML + 1, 99999])
def test_unusable_volume_falls_back_to_the_plain_component(millilitres):
    payload = {"components": [{"process": "coffee", "portion_ml": millilitres}]}
    result = _render(payload, "brew_finished")
    assert result.text == "Your drink is ready: coffee."
    # A glitched frame is an admission of incompleteness; a recipe that simply
    # states no volume is not.
    assert result.complete is (millilitres in (None, 0, -5))


def test_volume_is_a_bare_integer():
    payload = {"components": [{"process": "water", "portion_ml": 199.6}]}
    assert "200 ml of hot water" in _render(payload, "brew_finished").text


@pytest.mark.parametrize("raw,expected", [
    ("  Аня \U0001f49a<script> ", "Аня script"),
    ("…", ""),
    ("{drink}", "drink"),
    ("Anna-Maria", "Anna-Maria"),
    ("O'Brien", "O'Brien"),
    ("Dr. Anna", "Dr. Anna"),
    ("  Anna  ,", "Anna"),
    (None, ""),
    (42, ""),
    ("", ""),
    (" ​", ""),
])
def test_speakable_cases(raw, expected):
    assert _speakable(raw) == expected


def test_speakable_truncates_on_a_word_boundary():
    name = "Bartholomew Maximilian Fitzgerald the Third Espresso Blend"
    cleaned = _speakable(name)
    assert len(cleaned) <= 40
    assert name.startswith(cleaned)
    assert not cleaned.endswith(" ")


def test_profile_free_text_is_sanitised_into_the_sentence():
    payload = {"recipe_key": "espresso",
               "profile_name": "  Аня \U0001f49a<script> "}
    assert _render(payload, "brew_finished").text == (
        "Ready: Espresso — profile Аня script."
    )


# ---------------------------------------------------------------------------
# Drink resolution
# ---------------------------------------------------------------------------

def _ru_without_drink_overrides() -> dict[str, str]:
    """ru's narration map with the optional spoken-name family removed."""
    return {
        key: value for key, value in _narration("ru").items()
        if not key.startswith(NARRATION_DRINK_PREFIX)
    }


def test_spoken_override_beats_the_served_recipe_name():
    """Step 1 of the chain: the locale's own `narration.drink.*` wins.

    This is the whole point of M18. ru shows `Cappuccino` on a button and says
    `капучино` out loud, from two different assets, with no contradiction.
    """
    result = _render({"recipe_key": "cappuccino"}, "brew_started", "ru")
    assert result.text == "Готовлю: капучино."
    assert result.language == "ru"
    assert _ui("ru")["recipes.name.cappuccino"] == "Cappuccino"


def test_missing_override_falls_through_to_the_served_recipe_name():
    """Step 3: no override for this key -> the locale's own UI translation.

    Two ways to get here, and both are pinned: a locale that ships no override
    family at all, and the 10 ordinary nouns (`hot_water`, `milk_froth`, …) that
    every locale genuinely translates and that therefore have no override to
    ship. Neither case may drag the sentence into English.
    """
    stripped = _ru_without_drink_overrides()
    latte = _render({"recipe_key": "latte_macchiato"}, "brew_started", "ru",
                    narration_map=stripped)
    assert latte.text == "Готовлю: Latte Macchiato."
    assert latte.language == "ru"

    water = _render({"recipe_key": "hot_water"}, "brew_started", "ru")
    assert water.text == "Готовлю: Горячая вода."
    assert water.language == "ru"


def test_a_locale_without_the_drink_family_speaks_the_english_latin_name():
    """Step 2: the English overlay, which is where the 22 Latin locales land.

    A Latin-script locale omits the family on purpose, and must end up saying
    exactly the name its own UI shows — never the `.unnamed` head.
    """
    result = _render(CAPPUCCINO, "brew_finished", "de",
                     narration_map={
                         key: value for key, value in _narration("de").items()
                         if not key.startswith(NARRATION_DRINK_PREFIX)
                     })
    assert result.language == "de"
    assert "Cappuccino" in result.text
    assert result.key == "narration.event.brew_finished.named_detail"


def test_a_missing_override_is_never_a_gap_that_forces_english():
    """The family is optional, so its absence must not trip the overlay guard.

    A mandatory key going missing renders the whole sentence in English (§4.6
    level 2). A spoken name going missing must cost one word's pronunciation and
    nothing else — this is the assertion that keeps someone from "tidying" the
    drink keys into `NARRATION_KEYS`.
    """
    result = _render(CAPPUCCINO, "brew_finished", "ru",
                     narration_map=_ru_without_drink_overrides())
    assert result.language == "ru"
    assert result.complete is True
    assert result.missing_keys == ()
    assert result.text.startswith("Сварено: Cappuccino — 100 мл кофе")


def test_unknown_recipe_key_falls_through_to_the_free_text_name():
    payload = {"recipe_source": "sommelier", "recipe_key": "moon_latte",
               "recipe_name": "Moon Latte"}
    assert _render(payload, "brew_started").text == "Brewing: Moon Latte."


def test_a_machine_name_with_no_key_at_all_is_spoken_as_it_stands():
    """Step 4: a drink with no key in either family — freestyle and sommelier.

    There is no override and no `recipes.name.*` to find, so the sanitised name
    the machine reported is what gets spoken, in whatever language it was typed
    in; an unspeakable one demotes to the `.unnamed` head rather than leaving a
    dangling colon.
    """
    freestyle = _render({"recipe_name": "Ночной латте"}, "brew_started", "ru")
    assert freestyle.text == "Готовлю: Ночной латте."
    assert freestyle.language == "ru"

    assert _render({"recipe_name": "☕"}, "brew_started", "ru").text == (
        "Машина начала приготовление."
    )
    assert _render({}, "brew_started", "ru").key == (
        "narration.event.brew_started.unnamed"
    )


def test_directkey_category_token_resolves_through_the_name_map():
    """`milk` is a DirectKey category whose drink name key is `warm_milk`."""
    ui = {"recipes.name.warm_milk": "Warm Milk"}
    result = _render(
        {"recipe_source": "directkey", "recipe_key": "milk"},
        "brew_started",
        ui_map=ui,
    )
    assert result.text == "Brewing: Warm Milk."


def test_directkey_name_keys_cover_the_live_categories():
    """A new DirectKey category is a CI failure, not a silent "your drink"."""
    live = {category.name.lower() for category in DirectKeyCategory}
    assert set(DIRECTKEY_NAME_KEYS) == live
    assert narration.directkey_category_tokens() == live
    en_ui = _ui("en")
    for name_key in DIRECTKEY_NAME_KEYS.values():
        assert f"recipes.name.{name_key}" in en_ui


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def test_prompt_uses_the_served_manipulation_label():
    result = _render({"prompt": "FILL_WATER"}, "prompt_raised", "ru")
    assert result.text == "Машине нужна помощь: Долейте воду."
    assert result.key == "narration.event.prompt_raised"


def test_missing_manipulation_label_falls_back_to_the_generic_head():
    result = _render({"prompt": "FILL_WATER"}, "prompt_raised", ui_map={})
    assert result.key == "narration.event.prompt_raised_generic"
    assert result.text == "The machine needs your attention."


def test_manipulation_none_is_never_narrated_as_a_prompt():
    result = _render({"prompt": "NONE"}, "prompt_raised")
    assert result.key == "narration.event.prompt_raised_generic"


def test_prompt_cleared_has_one_head():
    assert _render({"prompt": "FILL_WATER", "duration_s": 30},
                   "prompt_cleared").text == "All good, the machine can carry on."


def test_unknown_maintenance_process_says_nothing():
    """READY is not a procedure and has no authored sentence."""
    assert _render({"process": "READY"}, "maintenance_finished") == \
        narration.NO_NARRATION




# ---------------------------------------------------------------------------
# Shape — the fallback identity of a front-panel brew
# ---------------------------------------------------------------------------

SHAPE_PREFIX = "narration.event.brew_finished.shape."


@pytest.mark.parametrize("token, sentence", [
    ("coffee", "Your coffee is ready."),
    ("coffee_with_milk", "Your milk coffee is ready."),
    ("milk", "Your milk is ready."),
    ("water", "Your hot water is ready."),
])
def test_each_shape_replaces_the_unnamed_sentence(token, sentence):
    """The front-panel case: no name was staged, but the legs said this much."""
    result = _render({"source": "machine", "shape": token}, "brew_finished")
    assert result.text == sentence
    assert result.key == f"{SHAPE_PREFIX}{token}"
    assert result.complete is True


def test_the_four_shape_sentences_are_distinct():
    """Four meanings, four sentences — a shared one would lose the distinction."""
    spoken = {
        token: _render({"shape": token}, "brew_finished").text
        for token in ("coffee", "coffee_with_milk", "milk", "water")
    }
    assert len(set(spoken.values())) == 4
    assert all(text and text.endswith(".") for text in spoken.values())


def test_a_known_drink_name_wins_and_the_shape_stays_unspoken():
    """"Ready: Cappuccino - a milk coffee" is redundant and sounds broken."""
    payload = {**CAPPUCCINO, "shape": "coffee_with_milk"}
    result = _render(payload, "brew_finished")
    assert result.text == (
        "Ready: Cappuccino — 100 ml of coffee, 150 ml of hot milk, strong, "
        "profile Anna."
    )
    assert result.key == "narration.event.brew_finished.named_detail"
    assert "milk coffee" not in result.text


def test_a_bare_name_with_no_detail_still_beats_the_shape():
    result = _render({"recipe_key": "espresso", "shape": "coffee"}, "brew_finished")
    assert result.key == "narration.event.brew_finished.named"
    assert result.text == "Ready: Espresso."


def test_a_composition_the_payload_states_outright_beats_the_shape():
    """`.unnamed_detail` says more than a shape token ever can, so it wins.

    Unreachable from the front panel — a brew with components was staged by
    Home Assistant — but pinned so the ladder stays explicit.
    """
    payload = {"shape": "coffee", "components": [{"process": "coffee", "portion_ml": 40}]}
    result = _render(payload, "brew_finished")
    assert result.key == "narration.event.brew_finished.unnamed_detail"
    assert result.text == "Your drink is ready: 40 ml of coffee."


def test_neither_a_name_nor_a_shape_keeps_the_unnamed_sentence():
    """The pre-shape behaviour, unchanged, for a brew nothing could classify."""
    result = _render({"source": "machine"}, "brew_finished")
    assert result.key == "narration.event.brew_finished.unnamed"
    assert result.text == "Your drink is ready."


@pytest.mark.parametrize("shape", ["latte", "", "COFFEE", 7, None, True])
def test_an_unknown_shape_token_is_not_templated_into_a_sentence(shape):
    result = _render({"source": "machine", "shape": shape}, "brew_finished")
    assert result.key == "narration.event.brew_finished.unnamed"
    assert result.text == "Your drink is ready."


@pytest.mark.parametrize("kind", ["brew_started", "brew_cancelled"])
def test_shape_is_never_spoken_on_a_start_or_a_cancellation(kind):
    """The detector emits it on `brew_finished` alone; the narrator agrees."""
    result = _render({"source": "machine", "shape": "coffee"}, kind)
    assert result.key == f"narration.event.{kind}.unnamed"


def test_a_locale_carrying_the_shape_speaks_it_in_its_own_language():
    ru = {**_narration("ru"), f"{SHAPE_PREFIX}water": "Горячая вода готова."}
    result = _render({"source": "machine", "shape": "water"}, "brew_finished", "ru",
                     narration_map=ru)
    assert result.text == "Горячая вода готова."
    assert result.language == "ru"
    assert result.key == f"{SHAPE_PREFIX}water"


def test_a_missing_shape_key_degrades_to_the_locale_unnamed_sentence():
    """Level 4, not level 2: the sentence must NOT switch to English for this.

    A shape sentence is a nicety on top of `.unnamed`; losing it costs one word
    of information. Sending the whole sentence to English would cost the
    listener their language, which is the worse outcome — so the shape key is
    resolved per key, like the spoken drink name, and never joins the overlay
    guard's planned set.
    """
    without = {
        key: value for key, value in _narration("ru").items()
        if not key.startswith(SHAPE_PREFIX)
    }
    result = _render({"source": "machine", "shape": "milk"}, "brew_finished", "ru",
                     narration_map=without)
    assert result.text == "Напиток готов."
    assert result.key == "narration.event.brew_finished.unnamed"
    assert result.language == "ru"
    # Reported rather than swallowed: something the payload knew went unsaid.
    assert result.complete is False
    assert f"{SHAPE_PREFIX}milk" in result.missing_keys


def test_the_shape_family_is_mandatory_in_the_keyspace():
    """Unlike `narration.drink.*`, these four are not an optional family."""
    from custom_components.melitta_barista.lifecycle import BREW_SHAPE_TOKENS

    assert {f"{SHAPE_PREFIX}{token}" for token in BREW_SHAPE_TOKENS} <= narration_keys()


# ---------------------------------------------------------------------------
# The fallback chain
# ---------------------------------------------------------------------------

def test_a_missing_locale_key_renders_the_whole_sentence_in_english():
    """Never code-switched: half a Russian sentence in English is worse."""
    broken = dict(_narration("ru"))
    del broken["narration.component.milk.with_amount"]
    result = _render(CAPPUCCINO, "brew_finished", "ru", narration_map=broken)
    assert result.language == "en"
    assert result.complete is False
    assert result.text == (
        "Ready: Cappuccino — 100 ml of coffee, 150 ml of hot milk, strong, "
        "profile Anna."
    )
    assert "narration.component.milk.with_amount" in result.missing_keys


def test_a_broken_template_retries_in_english_then_gives_up():
    broken = {**_narration("ru"),
              "narration.event.brew_finished.named": "Готово: {0}"}
    result = _render({"recipe_key": "espresso"}, "brew_finished", "ru",
                     narration_map=broken)
    assert result.language == "en"
    assert result.text == "Ready: Espresso."

    hopeless = {**_narration("en"),
                "narration.event.brew_finished.named": "Ready: {0}"}
    assert render(
        {"recipe_key": "espresso"}, event_type="brew_finished", locale="en",
        narration=hopeless, narration_en=hopeless,
        ui_strings=_ui("en"), ui_strings_en=_ui("en"),
    ).text is None


def test_cold_string_cache_says_nothing_at_all():
    """Level 7: the event still fires, just without a description."""
    result = render(CAPPUCCINO, event_type="brew_finished", locale="ru")
    assert result == narration.NO_NARRATION
    assert result.text is None
    assert result.language == ""


def test_unknown_event_type_says_nothing():
    assert _render({}, "bean_hopper_opened") is narration.NO_NARRATION
    assert _render({}, None) is narration.NO_NARRATION


def test_event_type_may_ride_in_the_payload():
    """`event.py` may pass the type separately or leave it in the payload."""
    payload = {**CAPPUCCINO, "type": "brew_started"}
    assert _render(payload, None).text == "Brewing: Cappuccino."


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

HOSTILE_TEMPLATES = [
    "", "   ", "{}", "{{}}", "%s", "{0}", "{drink", "drink}", "{DRINK}",
    "{very_long_placeholder_name_indeed}", "{drink} {detail} {volume}",
]


@pytest.mark.parametrize("template", HOSTILE_TEMPLATES)
def test_substitute_never_raises(template):
    assert isinstance(substitute(template, {"drink": "X"}), str)


@pytest.mark.parametrize("template", HOSTILE_TEMPLATES)
def test_render_never_raises_on_a_hostile_template(template):
    strings = {key: template for key in narration_keys()}
    result = render(
        CAPPUCCINO, event_type="brew_finished", locale="ru",
        narration=strings, narration_en=strings,
        ui_strings=_ui("en"), ui_strings_en=_ui("en"),
    )
    assert isinstance(result, Narration)


@pytest.mark.parametrize("payload", [
    {}, {"components": "not a list"}, {"components": [None, 5, "x"]},
    {"components": [{"process": 7, "portion_ml": "lots"}]},
    {"recipe_key": 5, "recipe_name": object()},
    {"profile_name": 42, "two_cups": "yes"},
    {"phase_index": "1", "phase_total": True},
    {"prompt": 3}, {"process": None}, {"shape": 7}, {"shape": "nope"},
    {"components": [{"process": "coffee", "portion_ml": float("inf")}]},
])
def test_render_never_raises_on_a_hostile_payload(payload):
    for event_type in (
        "brew_started", "brew_finished", "brew_cancelled",
        "prompt_raised", "prompt_cleared", "maintenance_finished",
    ):
        result = _render(payload, event_type)
        assert isinstance(result, Narration)
        assert result.text is None or "{" not in result.text


def test_render_never_raises_on_non_mapping_string_maps():
    result = render(CAPPUCCINO, event_type="brew_finished", locale="ru",
                    narration=None, narration_en=None,
                    ui_strings=None, ui_strings_en=None)
    assert result is narration.NO_NARRATION


# ---------------------------------------------------------------------------
# Placeholders and the not-served guarantee
# ---------------------------------------------------------------------------

def test_narration_placeholder_set_is_the_seven():
    """Frozen once shipped. `{ml}` is deliberately not one of them."""
    assert NARRATION_PLACEHOLDERS == frozenset({
        "{drink}", "{detail}", "{volume}", "{profile}", "{n}", "{m}", "{prompt}",
    })
    assert "{ml}" not in NARRATION_PLACEHOLDERS


def test_served_known_placeholders_are_untouched():
    """The served `ui_strings` placeholder set stays at its six members.

    Imported read-only from the ui_strings asset test: narration adding a
    placeholder name there would mean the family leaked into the served bundle
    (§4.0 point 2).
    """
    from tests.test_ui_contract_i18n_assets import KNOWN_PLACEHOLDERS

    assert KNOWN_PLACEHOLDERS == frozenset(
        {"{n}", "{m}", "{cup}", "{ml}", "{sec}", "{prompt}"}
    )
    assert len(KNOWN_PLACEHOLDERS) == 6


def test_narration_keys_never_appear_in_a_served_asset():
    """The leak guard: narration is server-side only (M1 / §4.0).

    `ui_strings/` is the WS-served bundle, and `strings.json` / `translations/`
    are hassfest's closed schema — a narration key in either is a shipped
    mistake that cannot be taken back for the life of contract_version 1.
    """
    for path in sorted(UI_STRINGS_DIR.glob("*.json")):
        leaked = [key for key in _load(path) if key.startswith("narration")]
        assert not leaked, f"{path.name} carries narration keys: {leaked}"

    served = [COMPONENT_DIR / "strings.json", *sorted(TRANSLATIONS_DIR.glob("*.json"))]
    for path in served:
        raw = path.read_text(encoding="utf-8")
        assert "narration" not in raw, f"{path.name} mentions narration"


def test_narration_is_not_an_i18n_domain():
    """`melitta_barista/i18n/get` can never serve one of these strings."""
    from custom_components.melitta_barista import panel_api

    assert "narration" not in panel_api._I18N_DOMAINS
    assert len(panel_api._I18N_DOMAINS) == 7


# ---------------------------------------------------------------------------
# The asset loader
# ---------------------------------------------------------------------------

def test_loader_resolves_exact_base_and_fallback():
    resolved, locale_map, en_map = load_narration_strings("ru")
    assert resolved == "ru"
    assert locale_map and en_map
    assert locale_map is not en_map

    assert load_narration_strings("de-DE")[0] == "de"
    assert load_narration_strings("xx")[0] == "en"
    assert load_narration_strings("")[0] == "en"
    assert load_narration_strings("../../etc/passwd")[0] == "en"


def test_loader_returns_the_raw_locale_file(monkeypatch, tmp_path):
    """No English merge: the overlay guard has to be able to see the gaps."""
    (tmp_path / "en.json").write_text(
        json.dumps({"narration.two_cups": "two cups",
                    "narration.profile": "profile {profile}"}),
        encoding="utf-8",
    )
    (tmp_path / "zz.json").write_text(
        json.dumps({"narration.two_cups": "zwei"}), encoding="utf-8"
    )
    monkeypatch.setattr(narration, "NARRATION_STRINGS_DIR", tmp_path)
    resolved, locale_map, en_map = load_narration_strings("zz")
    assert resolved == "zz"
    assert locale_map == {"narration.two_cups": "zwei"}
    assert "narration.profile" in en_map


def test_loader_survives_a_malformed_asset(monkeypatch, tmp_path):
    (tmp_path / "en.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(narration, "NARRATION_STRINGS_DIR", tmp_path)
    assert load_narration_strings("ru") == ("en", {}, {})


# ---------------------------------------------------------------------------
# The 29-locale property test
# ---------------------------------------------------------------------------

# CAPPED DELIBERATELY: a full slot power set is ~30 000 renders and exceeds the
# project's 10 s per-test timeout (pyproject.toml `timeout = 10`). This is a
# fixed, named list of 27 cases — 18 brew cases, each rendered for all three
# brew heads, plus 9 non-brew cases = 63 renders per locale, 1827 in total.
# Add cases here rather than generating combinations.
_COFFEE = {"process": "coffee", "portion_ml": 100}
_MILK = {"process": "milk", "portion_ml": 150}

_BREW_CASES: list[tuple[str, dict]] = [
    ("all_absent", {}),
    ("named_only", {"recipe_key": "espresso"}),
    ("all_present", {
        "recipe_key": "cappuccino", "profile_name": "Anna", "two_cups": True,
        "phase_index": 1, "phase_total": 3,
        "components": [
            {**_COFFEE, "intensity": "very_strong", "aroma": "intense",
             "temperature": "high", "shots": "three", "blend": "hopper_2"},
            _MILK,
        ],
    }),
    ("phase_only", {"recipe_key": "espresso", "phase_index": 0, "phase_total": 2}),
    ("component1_only", {"recipe_key": "espresso", "components": [_COFFEE]}),
    ("component2_only", {"recipe_key": "espresso", "components": [_COFFEE, _MILK]}),
    ("intensity_only", {"recipe_key": "espresso",
                        "components": [{**_COFFEE, "intensity": "very_mild"}]}),
    ("aroma_only", {"recipe_key": "espresso",
                    "components": [{**_COFFEE, "aroma": "intense"}]}),
    ("temperature_only", {"recipe_key": "espresso",
                          "components": [{**_COFFEE, "temperature": "cold"}]}),
    ("shots_only", {"recipe_key": "espresso",
                    "components": [{**_COFFEE, "shots": "one"}]}),
    ("blend_only", {"recipe_key": "espresso",
                    "components": [{**_COFFEE, "blend": "hopper_1"}]}),
    ("two_cups_only", {"recipe_key": "espresso", "two_cups": True}),
    ("profile_only", {"recipe_key": "espresso", "profile_name": "Anna-Maria"}),
    ("unnamed_with_detail", {"components": [_COFFEE, _MILK], "two_cups": True}),
    ("phase_without_drink", {"phase_index": 2, "phase_total": 3,
                             "components": [_MILK]}),
    ("free_text_name", {"recipe_name": "Moon Latte", "components": [_COFFEE]}),
    # Milk first, coffee second — the component order a latte macchiato really
    # reports, and the one that used to narrate the milk's filler intensity.
    ("milk_first", {
        "recipe_key": "latte_macchiato",
        "components": [
            {**_MILK, "intensity": "medium", "shots": "none"},
            {**_COFFEE, "intensity": "very_strong", "shots": "two"},
        ],
    }),
    # No coffee at all: nothing may attach a strength to hot water.
    ("no_coffee_component", {
        "recipe_key": "water",
        "components": [{"process": "water", "portion_ml": 150,
                        "intensity": "medium"}],
    }),
]

_OTHER_CASES: list[tuple[str, str, dict]] = [
    ("prompt_known", "prompt_raised", {"prompt": "FILL_WATER"}),
    # The longest served prompt in every locale (46 chars in el) — the case that
    # exercises NARRATION_MAX_LABEL rather than the free-text budget.
    ("prompt_longest", "prompt_raised", {"prompt": "MOVE_CUP_TO_FROTHER"}),
    ("prompt_unknown", "prompt_raised", {"prompt": "SOMETHING_NEW"}),
    ("prompt_none", "prompt_raised", {"prompt": "NONE"}),
    ("prompt_cleared", "prompt_cleared", {"prompt": "FILL_WATER", "duration_s": 9}),
    ("maintenance_cleaning", "maintenance_finished", {"process": "CLEANING"}),
    ("maintenance_descaling", "maintenance_finished", {"process": "DESCALING"}),
    ("maintenance_filter", "maintenance_finished", {"process": "FILTER_REPLACE"}),
    ("maintenance_steam", "maintenance_finished", {"process": "EVAPORATING"}),
]

assert len(_BREW_CASES) + len(_OTHER_CASES) == 27

SHIPPED_LOCALES = sorted(path.stem for path in UI_STRINGS_DIR.glob("*.json"))


@pytest.mark.parametrize("locale", SHIPPED_LOCALES)
def test_every_locale_renders_clean_sentences(locale):
    """Every shipped locale, over the capped slot matrix, speaks cleanly."""
    strings = _narration(locale)
    en_strings = _narration("en")
    ui, en_ui = _ui(locale), _ui("en")

    cases = [
        (f"{name}/{kind}", kind, payload)
        for name, payload in _BREW_CASES
        for kind in ("brew_started", "brew_finished", "brew_cancelled")
    ] + list(_OTHER_CASES)

    for label, event_type, payload in cases:
        result = render(payload, event_type=event_type, locale=locale,
                        narration=strings, narration_en=en_strings,
                        ui_strings=ui, ui_strings_en=en_ui)
        where = f"{locale}/{label}"
        assert result.text, f"{where}: empty sentence"
        text = result.text
        assert result.language == locale, f"{where}: fell back to {result.language}"
        assert "{" not in text and "}" not in text, f"{where}: {text}"
        assert ", ," not in text, f"{where}: {text}"
        assert " ." not in text, f"{where}: {text}"
        assert " —." not in text and " -." not in text, f"{where}: {text}"
        assert "  " not in text, f"{where}: {text}"
        assert text == text.strip(), f"{where}: {text}"


def _longest_manipulation_label(ui: dict[str, str]) -> tuple[str, str]:
    """The locale's longest served `status.manipulation.*` (token, label) pair."""
    labels = {
        key.rsplit(".", 1)[1]: value
        for key, value in ui.items()
        if key.startswith("status.manipulation.") and value
    }
    labels.pop("NONE", None)
    token = max(labels, key=lambda name: (len(labels[name]), name))
    return token, labels[token]


@pytest.mark.parametrize("locale", SHIPPED_LOCALES)
def test_the_longest_served_prompt_is_spoken_untruncated(locale):
    """`{prompt}` gets the label budget, never the free-text one.

    `NARRATION_MAX_LABEL` exists for exactly this: `status.manipulation.*` is
    authored by us and el's `MOVE_CUP_TO_FROTHER` is 46 characters, so clipping
    it to `NARRATION_MAX_FREE_TEXT` would tell a Greek listener to move the cup
    "to the nozzle" — dropping the word that says which nozzle. lt and mk clip
    too. Every other prompt in this suite is short enough to survive either
    budget, so without this case the constant can be halved unnoticed.
    """
    token, label = _longest_manipulation_label(_ui(locale))
    result = _render({"prompt": token}, "prompt_raised", locale)
    assert result.key == "narration.event.prompt_raised"
    assert label in result.text, f"{locale}: {label!r} clipped in {result.text!r}"


def test_the_prompt_budget_is_the_one_the_shipped_labels_need():
    """Pins both ends: the label budget is load-bearing and it is sufficient."""
    longest = max(
        len(_longest_manipulation_label(_ui(locale))[1])
        for locale in SHIPPED_LOCALES
    )
    assert longest > NARRATION_MAX_FREE_TEXT
    assert longest <= NARRATION_MAX_LABEL
