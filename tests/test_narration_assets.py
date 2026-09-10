"""Tests for the `narration_strings/` asset files (spec §4.2/§4.5/§4.8).

The narration analogue of `tests/test_ui_contract_i18n_assets.py`, and it has to
exist separately because narration is **not** served: it is absent from
`_I18N_DOMAINS`, from `strings_version` and from `contract_fingerprint`, so not
one of the ui_strings asset tests covers these 29 files. If an assertion here
ever has to move into the ui_strings test module, narration has leaked into the
served bundle — fix the leak, do not move the assertion (§4.0 point 5).

The completeness rule is stricter here than for `ui_strings/`: for the 41
mandatory keys there is no sparse allowance. A missing one does not fall back
per key; it makes the renderer emit the **whole** sentence in English (§4.6's
overlay guard), so a sparse narration locale is a locale that silently stops
speaking its own language.

The single carve-out is `narration.drink.*` (M18), the 22 spoken drink-name
overrides. That family alone is **optional per locale** and falls back per key,
so the rules for it are different and are spelled out one by one below:

* `en` carries all 22, verbatim from `ui_strings/en.json` — so the family is an
  identity in English and the "subset of en" parity rule still holds;
* a locale carrying **any** of the 22 must carry **all** 22 (no half-spoken
  drink lists);
* bg, el, mk, ru, sr and uk are positively pinned as carrying all 22, in their
  own script — that is the whole point of the family, since those six spell the
  same 22 names in Latin script in `ui_strings/`, which a TTS voice cannot say.

None of this touches `ui_strings/` or `SPARSE_EXEMPT_KEYS`, which stays empty:
`narration_strings/` is a separate, unserved asset directory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.melitta_barista.narration import (
    NARRATION_DRINK_PREFIX,
    NARRATION_PLACEHOLDERS,
    narration_all_keys,
    narration_drink_keys,
    narration_keys,
)

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "melitta_barista"
NARRATION_DIR = COMPONENT_DIR / "narration_strings"
UI_STRINGS_DIR = COMPONENT_DIR / "ui_strings"

# Any `{...}` span, whether or not it is a known placeholder — a renamed
# placeholder must fail the comparison, not slip past it as "none found".
_PLACEHOLDER_SPAN = re.compile(r"\{[^}]*\}")

_LOWER_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Basic Latin plus the Latin-1/Extended-A ranges — enough to catch "Cappuccino"
# and "Café Crème" left untranslated in a Cyrillic or Greek file.
_LATIN_LETTER = re.compile(r"[A-Za-zÀ-ɏ]")

TERMINAL_PUNCTUATION = (".", "!", "?", "…")

# The full narration key of every optional spoken drink name.
DRINK_KEYS = frozenset(
    f"{NARRATION_DRINK_PREFIX}{key}" for key in narration_drink_keys()
)

# The six locales that do not write in Latin script. `ui_strings/` spells all 22
# proper names in Latin for them by house convention (correct on a button, wrong
# in a sentence), so these are exactly the locales that MUST override.
NON_LATIN_SCRIPT_LOCALES = ("bg", "el", "mk", "ru", "sr", "uk")

# The clause families: fragments that the server joins with ", " and drops into
# a head template. Rule 4 of §4.5 is mechanical about their shape.
CLAUSE_PREFIXES = (
    "narration.component.", "narration.intensity.", "narration.aroma.",
    "narration.temperature.", "narration.shots.", "narration.blend.",
    "narration.two_cups", "narration.profile", "narration.phase",
)

# §4.2 family sizes: 19 + 6 + 5 + 1 + 2 + 3 + 2 + 1 + 1 + 1 = 41 mandatory,
# plus the 22 optional `narration.drink.*` = 63 keys in en.json. The `event` row
# is the loud-CI-failure guard: a new `MachineProcess` member means a new
# maintenance sentence nobody has authored yet.
MANDATORY_TOTAL = 41
DRINK_TOTAL = 22
EN_TOTAL = MANDATORY_TOTAL + DRINK_TOTAL

FAMILY_COUNTS = {
    "narration.event.": 19,
    "narration.drink.": DRINK_TOTAL,
    "narration.component.": 6,
    "narration.intensity.": 5,
    "narration.aroma.": 1,
    "narration.temperature.": 2,
    "narration.shots.": 3,
    "narration.blend.": 2,
    "narration.two_cups": 1,
    "narration.profile": 1,
    "narration.phase": 1,
}

# §4.3: the placeholder set of every single key, pinned explicitly. Losing one
# would render a sentence without its value; gaining one would render a literal
# brace, which the renderer answers by falling back to English.
EXPECTED_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "narration.event.brew_started.named": frozenset({"{drink}"}),
    "narration.event.brew_started.unnamed": frozenset(),
    "narration.event.brew_finished.named": frozenset({"{drink}"}),
    "narration.event.brew_finished.named_detail": frozenset({"{drink}", "{detail}"}),
    "narration.event.brew_finished.unnamed": frozenset(),
    "narration.event.brew_finished.unnamed_detail": frozenset({"{detail}"}),
    "narration.event.brew_cancelled.named": frozenset({"{drink}"}),
    "narration.event.brew_cancelled.unnamed": frozenset(),
    "narration.event.prompt_raised": frozenset({"{prompt}"}),
    "narration.event.prompt_raised_generic": frozenset(),
    "narration.event.prompt_cleared": frozenset(),
    "narration.event.maintenance_finished.CLEANING": frozenset(),
    "narration.event.maintenance_finished.DESCALING": frozenset(),
    "narration.event.maintenance_finished.EASY_CLEAN": frozenset(),
    "narration.event.maintenance_finished.INTENSIVE_CLEAN": frozenset(),
    "narration.event.maintenance_finished.FILTER_INSERT": frozenset(),
    "narration.event.maintenance_finished.FILTER_REPLACE": frozenset(),
    "narration.event.maintenance_finished.FILTER_REMOVE": frozenset(),
    "narration.event.maintenance_finished.EVAPORATING": frozenset(),
    "narration.component.coffee.with_amount": frozenset({"{volume}"}),
    "narration.component.coffee.plain": frozenset(),
    "narration.component.milk.with_amount": frozenset({"{volume}"}),
    "narration.component.milk.plain": frozenset(),
    "narration.component.water.with_amount": frozenset({"{volume}"}),
    "narration.component.water.plain": frozenset(),
    "narration.intensity.very_mild": frozenset(),
    "narration.intensity.mild": frozenset(),
    "narration.intensity.medium": frozenset(),
    "narration.intensity.strong": frozenset(),
    "narration.intensity.very_strong": frozenset(),
    "narration.aroma.intense": frozenset(),
    "narration.temperature.cold": frozenset(),
    "narration.temperature.high": frozenset(),
    "narration.shots.one": frozenset(),
    "narration.shots.two": frozenset(),
    "narration.shots.three": frozenset(),
    "narration.blend.hopper_1": frozenset(),
    "narration.blend.hopper_2": frozenset(),
    "narration.two_cups": frozenset(),
    "narration.profile": frozenset({"{profile}"}),
    "narration.phase": frozenset({"{n}", "{m}"}),
}

# A spoken drink name is a bare noun phrase substituted into `{drink}`; none of
# the 22 may ever carry a placeholder of its own. Generated rather than typed
# out 22 times because there is nothing per-key to state — the interesting pin
# is `test_narration_drink_names_are_bare_names`, which enforces the shape.
EXPECTED_PLACEHOLDERS.update({key: frozenset() for key in DRINK_KEYS})


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _locale_names(directory: Path) -> set[str]:
    return {path.stem for path in directory.glob("*.json")}


def _spans(value: str) -> list[str]:
    """Placeholder spans of a string, order-insensitive but count-sensitive."""
    return sorted(_PLACEHOLDER_SPAN.findall(value))


def _assert_flat_string_map(data, label):
    assert isinstance(data, dict), f"{label}: top level must be an object"
    for key, value in data.items():
        assert isinstance(key, str) and key.startswith("narration."), (
            f"{label}: key {key!r} is not a narration key"
        )
        assert isinstance(value, str) and value.strip(), (
            f"{label}: value for {key!r} must be a non-empty string"
        )


NON_EN_LOCALES = sorted(_locale_names(UI_STRINGS_DIR) - {"en"})


@pytest.fixture(scope="module")
def en_narration():
    """The English narration map — the normative baseline."""
    return _load(NARRATION_DIR / "en.json")


# ---------------------------------------------------------------------------
# Shape and completeness
# ---------------------------------------------------------------------------

def test_locale_set_matches_ui_strings():
    """narration_strings/ ships the same 29 locales as the served bundle."""
    assert _locale_names(NARRATION_DIR) == _locale_names(UI_STRINGS_DIR)
    assert len(_locale_names(NARRATION_DIR)) == 29


def test_en_is_flat_string_map(en_narration):
    _assert_flat_string_map(en_narration, "en.json")


def test_en_is_exactly_the_derived_keyspace(en_narration):
    """en.json == 41 mandatory + 22 optional drink names, no orphans either way."""
    assert set(en_narration) == narration_all_keys()
    assert len(en_narration) == EN_TOTAL

    # The two halves, separately, so a miscount lands in the right one.
    assert narration_keys() <= set(en_narration)
    assert len(narration_keys()) == MANDATORY_TOTAL
    assert DRINK_KEYS <= set(en_narration)
    assert len(DRINK_KEYS) == DRINK_TOTAL


def test_narration_family_counts_pinned(en_narration):
    """§4.2's family table, per family, so a new key lands in a named row."""
    counts = {
        prefix: sum(
            1 for key in en_narration
            if key == prefix or key.startswith(prefix)
        )
        for prefix in FAMILY_COUNTS
    }
    assert counts == FAMILY_COUNTS
    assert sum(FAMILY_COUNTS.values()) == EN_TOTAL
    assert sum(
        count for prefix, count in FAMILY_COUNTS.items()
        if prefix != "narration.drink."
    ) == MANDATORY_TOTAL


def test_maintenance_sentences_cover_the_live_process_table():
    """A new `MachineProcess` member must fail CI, not narrate nothing.

    The eight maintenance heads are derived from the live enum by subtraction
    (READY / PRODUCT / BUSY / SWITCH_OFF are not procedures), which is the whole
    reason this family is enumerated instead of templated.
    """
    from custom_components.melitta_barista.lifecycle import MAINTENANCE_PROCESSES

    expected = {
        f"narration.event.maintenance_finished.{process.name}"
        for process in MAINTENANCE_PROCESSES
    }
    assert expected <= narration_keys()
    assert len(expected) == 8


@pytest.mark.parametrize("locale", NON_EN_LOCALES)
def test_locale_is_complete_and_invents_nothing(locale, en_narration):
    """All 41 mandatory keys in all 28 non-English locales, and no orphans.

    Unlike the served bundle there is no per-key English overlay for these: one
    missing key sends the *whole* sentence to English (§4.6 fallback level 2),
    so a sparse file is a locale that stopped speaking its own language.

    `narration.drink.*` is the one family exempt from the completeness half of
    this rule (M18) — it is optional per locale and falls back per key. It is
    **not** exempt from the "invents nothing" half, and its own all-or-nothing
    and six-locale rules are enforced right below.
    """
    data = _load(NARRATION_DIR / f"{locale}.json")
    _assert_flat_string_map(data, f"{locale}.json")
    assert narration_keys() <= set(data), (
        f"{locale}.json: missing mandatory {sorted(narration_keys() - set(data))}"
    )
    assert set(data) <= set(en_narration), (
        f"{locale}.json: invented {sorted(set(data) - set(en_narration))}"
    )


# ---------------------------------------------------------------------------
# The optional spoken-drink-name family (M18)
# ---------------------------------------------------------------------------

def test_en_drink_names_are_verbatim_copies_of_the_served_recipe_names():
    """The English half of the family is an identity, and provably so.

    en exists to be the overlay every Latin-script locale falls through to, so
    each of the 22 must equal `ui_strings/en.json`'s `recipes.name.<key>`
    character for character. If the two ever drift, a Latin-script locale starts
    speaking a name its own UI does not show.
    """
    en_narration = _load(NARRATION_DIR / "en.json")
    en_ui = _load(UI_STRINGS_DIR / "en.json")
    mismatched = {
        key: (en_narration[key], en_ui.get(f"recipes.name.{key[len(NARRATION_DRINK_PREFIX):]}"))
        for key in sorted(DRINK_KEYS)
        if en_narration[key]
        != en_ui.get(f"recipes.name.{key[len(NARRATION_DRINK_PREFIX):]}")
    }
    assert not mismatched, f"en drink names drifted from ui_strings: {mismatched}"


@pytest.mark.parametrize("locale", sorted(_locale_names(UI_STRINGS_DIR)))
def test_drink_family_is_all_or_nothing(locale):
    """A locale carrying any of the 22 spoken names must carry all 22.

    Half a family is the one outcome worse than none: the sentence would speak
    some drinks in the local script and others in Latin, from the same voice, in
    the same kitchen.
    """
    present = DRINK_KEYS & set(_load(NARRATION_DIR / f"{locale}.json"))
    assert present in (frozenset(), DRINK_KEYS), (
        f"{locale}.json carries {len(present)}/{DRINK_TOTAL} spoken drink names; "
        f"missing {sorted(DRINK_KEYS - present)}"
    )


@pytest.mark.parametrize("locale", NON_LATIN_SCRIPT_LOCALES)
def test_non_latin_script_locales_carry_spoken_drink_names(locale):
    """bg/el/mk/ru/sr/uk MUST override all 22, in their own script.

    These six are the reason the family exists. Their `ui_strings` spell the 22
    proper names in Latin — right on a picker button, unsayable by a Cyrillic or
    Greek TTS voice — so a Latin value here means the override was copied rather
    than written and buys the listener nothing.
    """
    data = _load(NARRATION_DIR / f"{locale}.json")
    missing = sorted(DRINK_KEYS - set(data))
    assert not missing, f"{locale}.json is missing spoken drink names: {missing}"

    latin = {
        key: data[key] for key in sorted(DRINK_KEYS)
        if _LATIN_LETTER.search(data[key])
    }
    assert not latin, (
        f"{locale}.json still spells these in Latin script: {latin}"
    )


@pytest.mark.parametrize("locale", sorted(_locale_names(UI_STRINGS_DIR)))
def test_narration_drink_names_are_bare_names(locale):
    """A spoken name is a noun phrase: no placeholder, no edge space, no stop.

    It is substituted into `{drink}` inside a head template that owns the
    sentence's punctuation, exactly like a clause fragment.
    """
    data = _load(NARRATION_DIR / f"{locale}.json")
    for key in sorted(DRINK_KEYS & set(data)):
        value = data[key]
        assert not _spans(value), f"{locale}/{key}: placeholders in a drink name"
        assert value == value.strip(), f"{locale}/{key}: edge whitespace"
        assert not value.endswith(TERMINAL_PUNCTUATION), (
            f"{locale}/{key}: {value!r} ends a sentence"
        )
        assert not value.endswith(","), f"{locale}/{key}: trailing comma"


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------

def test_narration_en_placeholders_pinned(en_narration):
    """Every English string's placeholder set, key by key."""
    assert set(EXPECTED_PLACEHOLDERS) == set(en_narration)
    actual = {key: frozenset(_spans(value)) for key, value in en_narration.items()}
    assert actual == EXPECTED_PLACEHOLDERS


@pytest.mark.parametrize("locale", NON_EN_LOCALES)
def test_narration_locale_carries_placeholders_verbatim(locale, en_narration):
    """Translators may reorder placeholders; renaming or dropping one is a bug.

    tr's `{profile} profili` is the reordering this rule exists to allow; a
    dropped `{volume}` would announce "ml of coffee" with no number.
    """
    data = _load(NARRATION_DIR / f"{locale}.json")
    mismatched = {
        key: (_spans(value), _spans(en_narration[key]))
        for key, value in data.items()
        if key in en_narration and _spans(value) != _spans(en_narration[key])
    }
    assert not mismatched, f"{locale}.json placeholder drift: {mismatched}"


@pytest.mark.parametrize("locale", sorted(_locale_names(UI_STRINGS_DIR)))
def test_narration_uses_no_unknown_placeholders(locale):
    """Every `{...}` span in every locale is one of the seven (§4.3).

    `{ml}` in particular must never appear: in the served `wizard.step.cup` it
    means a pre-formatted "200 ml", and one placeholder name keeps one meaning
    repo-wide.
    """
    data = _load(NARRATION_DIR / f"{locale}.json")
    unknown = {
        key: [span for span in _spans(value) if span not in NARRATION_PLACEHOLDERS]
        for key, value in data.items()
        if any(span not in NARRATION_PLACEHOLDERS for span in _spans(value))
    }
    assert not unknown, f"{locale}.json uses unknown placeholders: {unknown}"


# ---------------------------------------------------------------------------
# Key and value shape
# ---------------------------------------------------------------------------

def test_narration_key_casing_pinned(en_narration):
    """Only the `MachineProcess` token is UPPER_SNAKE; everything else is lower."""
    upper_segments = []
    for key in en_narration:
        segments = key.split(".")
        assert segments[0] == "narration"
        for index, segment in enumerate(segments[1:], start=1):
            if _UPPER_SNAKE.match(segment):
                upper_segments.append((key, segment))
                assert segments[index - 1] == "maintenance_finished", (
                    f"{key}: unexpected UPPER_SNAKE segment {segment!r}"
                )
            else:
                assert _LOWER_SNAKE.match(segment), f"{key}: bad segment {segment!r}"
    assert len(upper_segments) == 8


@pytest.mark.parametrize("locale", sorted(_locale_names(UI_STRINGS_DIR)))
def test_narration_clauses_have_no_edge_whitespace_or_punctuation(locale):
    """§4.5 rule 4, mechanically: the server owns the separator and the stop."""
    data = _load(NARRATION_DIR / f"{locale}.json")
    for key, value in data.items():
        if not key.startswith(CLAUSE_PREFIXES):
            continue
        assert value == value.strip(), f"{locale}/{key}: edge whitespace"
        assert not value.startswith(","), f"{locale}/{key}: leading comma"
        assert not value.endswith(","), f"{locale}/{key}: trailing comma"
        assert not value.endswith("."), f"{locale}/{key}: terminal full stop"


@pytest.mark.parametrize("locale", sorted(_locale_names(UI_STRINGS_DIR)))
def test_narration_event_heads_end_in_terminal_punctuation(locale):
    """A head is a whole sentence, so TTS must not run two of them together."""
    data = _load(NARRATION_DIR / f"{locale}.json")
    for key, value in data.items():
        if not key.startswith("narration.event."):
            continue
        assert value.rstrip().endswith(TERMINAL_PUNCTUATION), (
            f"{locale}/{key}: {value!r} does not end a sentence"
        )
