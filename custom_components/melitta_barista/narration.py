"""Server-side narration: one spoken sentence per machine lifecycle event.

`event.py` fires a structured, token-only payload (`lifecycle.py` §2.4). This
module turns that payload into a single localized sentence which rides along as
the event's ``description`` attribute, so a household can pipe it straight into
`tts.speak` or read it in the logbook without teaching an automation template
how to say "150 ml of hot milk" in Finnish.

Why the strings are NOT part of the UI Contract
-----------------------------------------------
These sentences are rendered **here, on the server**, and no client renders
them. UI-Contract §5.2 rule 1 makes any *served* string family unremovable,
unrenamable and un-re-meanable for the life of `contract_version 1`, so
shipping 45 mandatory keys x 29 locales into three clients' persisted caches
would buy nothing and could never be undone. They therefore live in their own
asset directory, `narration_strings/<locale>.json`:

* not in `ui_strings/` and not a member of `panel_api._I18N_DOMAINS`, so
  `melitta_barista/i18n/get` can never return one;
* absent from `strings_version` and therefore from `contract_fingerprint`;
* not in `strings.json` / `translations/` either — hassfest's schema is closed
  and would reject both a new top-level category and the UPPER_SNAKE process
  tokens.

`tests/test_narration.py::test_narration_keys_never_appear_in_a_served_asset`
and its two neighbours are the mechanical guard for all three.

The one idea
------------
**Enum values are never interpolated as words. Every (slot, token) pair is its
own complete, translated clause. The only things ever substituted are numbers
and free text.** No case, gender or number agreement is ever computed by code
and nothing is ever pluralised: Russian gets `Очистка завершена` /
`Выпаривание завершено` / `Фильтр заменён` because those are three authored
sentences, not one template with a noun dropped into it.

Consequences that the rest of this module exists to guarantee:

* an unknown token (new firmware) drops exactly one clause and leaves the
  sentence grammatical — it never leaves a dangling preposition;
* a locale file missing a key renders the **whole** sentence in English rather
  than code-switching mid-sentence, and says so in `Narration.language`;
* free text (profile and freestyle names) is sanitised before it is spoken;
* `render()` can never raise. It runs off a BLE status callback, and narration
  must never be able to stop an event from firing.

The one exception: spoken drink names
-------------------------------------
There is a single **optional** key family, `narration.drink.*`
(`NARRATION_DRINK_KEYS`, 22 keys), and it is the only thing in this module that
falls back per key rather than per sentence. It exists because the served
`recipes.name.*` strings a client shows on a button are the wrong string to hand
a text-to-speech engine in a non-Latin script. `_resolve_drink` documents the
chain; do not delete the family as redundant.

Calling convention (read this before wiring `event.py`)
-------------------------------------------------------
`render()` needs the event **type** as well as the payload, and the payload
`lifecycle.LifecycleEvent` carries does not contain it — the type lives beside
it, in `LifecycleEvent.type`. So pass it::

    narration.render(ev.payload, event_type=ev.type, locale=…, …)

A payload that already carries its own `type` (or `kind`) key works too, which
is what the bus-event dict does. With neither, `render` has nothing to render
and returns `NO_NARRATION`.

Purity
------
The renderer is pure: stdlib plus this package's own dependency-free token
tables (`ui_contract`, `const`, `lifecycle` — none of which import
`homeassistant`). There is no `hass`, no I/O and no logging of user text
anywhere on the render path. The only blocking function is
`load_narration_strings`, fenced at the bottom of the module and executor-only.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from .const import DirectKeyCategory
from .lifecycle import (
    BREW_SHAPE_TOKENS,
    EVENT_BREW_CANCELLED,
    EVENT_BREW_FINISHED,
    EVENT_BREW_STARTED,
    EVENT_MAINTENANCE_FINISHED,
    EVENT_PROMPT_CLEARED,
    EVENT_PROMPT_RAISED,
    MAINTENANCE_PROCESSES,
)
from .ui_contract import (
    FREESTYLE_AROMA_TOKENS,
    FREESTYLE_BLEND_TOKENS,
    FREESTYLE_INTENSITY_TOKENS,
    FREESTYLE_PROCESS_TOKENS,
    FREESTYLE_SHOTS_TOKENS,
    FREESTYLE_TEMPERATURE_TOKENS,
)

_LOGGER = logging.getLogger("melitta_barista")


# ---------------------------------------------------------------------------
# Normative constants
# ---------------------------------------------------------------------------

NARRATION_DOMAIN: Final = "narration"
"""Key prefix of every narration string. Deliberately NOT an `_I18N_DOMAINS`
member — grepping for it in a served asset is how a leak is caught."""

NARRATION_SEPARATOR: Final = ", "
"""How detail clauses are joined. A module constant, not a translated key:
29 identical `", "` strings would be a pure drift surface across the European
locale set this integration ships, and the separator can still be made
per-locale later without breaking a single stored client cache."""

NARRATION_MAX_CLAUSES: Final = 6
"""Clause budget for one sentence, so TTS stays under roughly 15 seconds.
Overflow drops the tail of `SLOT_ORDER` and reports `complete=False`."""

NARRATION_MAX_ML: Final = 2000
"""Above this a volume is treated as a glitched frame: the component falls back
to its `.plain` form rather than announcing "1275 ml of coffee"."""

NARRATION_MAX_FREE_TEXT: Final = 40
"""Character budget for user-authored free text (`{drink}`, `{profile}`)."""

NARRATION_MAX_LABEL: Final = 80
"""Character budget for a *served* label spoken verbatim (`{prompt}`). Larger
than the free-text budget on purpose: `status.manipulation.*` is authored by us
and el's longest prompt is 46 characters, which must not be truncated."""

NARRATION_OMITTED_TOKENS: Final[Mapping[str, frozenset[str]]] = {
    "temperature": frozenset({"normal"}),
    "shots": frozenset({"none"}),
    "aroma": frozenset({"standard"}),
    "process": frozenset({"none"}),
}
"""Tokens that are the machine default and are therefore never spoken.

Speaking them would lengthen every single sentence for no information. There is
deliberately **no `blend` entry**: `component_to_tokens` *omits* the `blend` key
for the machine-default hopper (wire byte 0) instead of emitting a token, so a
blend clause is produced exactly when the key is present. Adding `blend` here
would be a bug, not a tightening."""

NARRATION_PLACEHOLDERS: Final[frozenset[str]] = frozenset({
    "{drink}", "{detail}", "{volume}", "{profile}", "{n}", "{m}", "{prompt}",
})
"""The only placeholder names any narration string may use.

`{volume}` is a bare integer count of millilitres: the unit and its noun belong
inside the translator's clause so they can be inflected (ru genitive
`100 мл кофе`, fi partitive `100 ml kahvia`). `{ml}` is deliberately absent —
in the served `wizard.step.cup` it means a pre-formatted "200 ml", and one
placeholder name must keep one meaning repo-wide."""

SLOT_ORDER: Final[tuple[str, ...]] = (
    "phase", "component1", "component2", "intensity", "aroma",
    "temperature", "shots", "blend", "two_cups", "profile",
)
"""Clause order, fixed in code rather than per locale, so the sentence is
deterministic and testable. The tail is what a clause-budget overflow drops,
which is why the least informative slots sit at the end."""

DIRECTKEY_NAME_KEYS: Final[dict[str, str]] = {
    "espresso": "espresso",
    "cafe_creme": "cafe_creme",
    "cappuccino": "cappuccino",
    "latte_macchiato": "latte_macchiato",
    "milk_froth": "milk_froth",
    "milk": "warm_milk",
    "water": "hot_water",
}
"""DirectKey category token -> the `recipes.name.*` key that names the drink.

Keys are `DirectKeyCategory.<M>.name.lower()`; values are live `ui_strings`
keys. Pinned in both directions by `tests/test_narration.py`, so a new DirectKey
category fails CI instead of quietly narrating "your drink"."""

BREW_KINDS: Final[frozenset[str]] = frozenset({
    EVENT_BREW_STARTED, EVENT_BREW_FINISHED, EVENT_BREW_CANCELLED,
})
"""Event types whose sentence names a drink."""

_MAINTENANCE_TOKENS: Final[tuple[str, ...]] = tuple(
    sorted(process.name for process in MAINTENANCE_PROCESSES)
)

_COMPONENT_TOKENS: Final[tuple[str, ...]] = tuple(
    token for token in FREESTYLE_PROCESS_TOKENS
    if token not in NARRATION_OMITTED_TOKENS["process"]
)

# family -> the tokens that get their own clause key. Derived from the live
# contract vocabularies minus the machine defaults, never hand-listed: a new
# enum member becomes a missing key, which the asset test reports loudly.
_VALUE_FAMILIES: Final[Mapping[str, tuple[str, ...]]] = {
    "intensity": FREESTYLE_INTENSITY_TOKENS,
    "aroma": tuple(
        token for token in FREESTYLE_AROMA_TOKENS
        if token not in NARRATION_OMITTED_TOKENS["aroma"]
    ),
    "temperature": tuple(
        token for token in FREESTYLE_TEMPERATURE_TOKENS
        if token not in NARRATION_OMITTED_TOKENS["temperature"]
    ),
    "shots": tuple(
        token for token in FREESTYLE_SHOTS_TOKENS
        if token not in NARRATION_OMITTED_TOKENS["shots"]
    ),
    "blend": FREESTYLE_BLEND_TOKENS,
}

# The value slots, in SLOT_ORDER, and the component field each one reads.
_VALUE_SLOTS: Final[tuple[str, ...]] = (
    "intensity", "aroma", "temperature", "shots", "blend",
)

_COFFEE_PROCESS: Final = "coffee"

_COFFEE_ONLY_SLOTS: Final[frozenset[str]] = frozenset({
    "intensity", "aroma", "shots", "blend",
})
"""Value slots that describe the COFFEE and are read from no other component.

How strong the grind was, whether the aroma programme was intense, how many
extra shots went in and which hopper was used are all facts about the coffee
side of a drink. `component_to_tokens` still fills those fields in on a milk or
water component, from the wire defaults — `_INTENSITY_NAMES.get(byte, "medium")`
in particular manufactures a `"medium"` for a byte it does not know — so on
those components they are filler, not facts.

`temperature` is deliberately NOT here: it is a genuine per-component fact
(`high` on the milk component is what makes a drink froth-dominant, and
`ui_contract.build_icon_spec` reads it from exactly that component), so it keeps
the first-non-default-wins rule."""

# Head variants per brew kind: (named, unnamed, named_detail, unnamed_detail).
# Only `brew_finished` speaks the detail clauses; a start or a cancellation
# names the drink and stops, because nobody wants the full composition read out
# twice per cup.
_BREW_HEADS: Final[Mapping[str, tuple[str, str, str | None, str | None]]] = {
    EVENT_BREW_STARTED: (
        "narration.event.brew_started.named",
        "narration.event.brew_started.unnamed",
        None,
        None,
    ),
    EVENT_BREW_FINISHED: (
        "narration.event.brew_finished.named",
        "narration.event.brew_finished.unnamed",
        "narration.event.brew_finished.named_detail",
        "narration.event.brew_finished.unnamed_detail",
    ),
    EVENT_BREW_CANCELLED: (
        "narration.event.brew_cancelled.named",
        "narration.event.brew_cancelled.unnamed",
        None,
        None,
    ),
}

NARRATION_SHAPE_PREFIX: Final = "narration.event.brew_finished.shape."
"""Key prefix of the four shape sentences — one per `lifecycle.BREW_SHAPE_TOKENS`.

They are whole sentences with no placeholders, exactly like the maintenance
family, and they are MANDATORY in every locale: a shape sentence is the only
thing a front-panel brew can say beyond "your drink is ready", and a locale
missing one would fall back to precisely that."""

_SHAPE_KEYS: Final[Mapping[str, str]] = {
    token: f"{NARRATION_SHAPE_PREFIX}{token}" for token in BREW_SHAPE_TOKENS
}

_PROMPT_RAISED_KEY: Final = "narration.event.prompt_raised"
_PROMPT_GENERIC_KEY: Final = "narration.event.prompt_raised_generic"
_PROMPT_CLEARED_KEY: Final = "narration.event.prompt_cleared"

_MANIPULATION_NONE: Final = "NONE"


def _derive_narration_keys() -> frozenset[str]:
    """The 45 mandatory keys, derived from the live vocabularies."""
    keys: set[str] = set()
    for variants in _BREW_HEADS.values():
        keys.update(key for key in variants if key)
    keys.update(_SHAPE_KEYS.values())
    keys.update({_PROMPT_RAISED_KEY, _PROMPT_GENERIC_KEY, _PROMPT_CLEARED_KEY})
    for token in _MAINTENANCE_TOKENS:
        keys.add(f"narration.event.maintenance_finished.{token}")
    for token in _COMPONENT_TOKENS:
        keys.add(f"narration.component.{token}.with_amount")
        keys.add(f"narration.component.{token}.plain")
    for family, tokens in _VALUE_FAMILIES.items():
        for token in tokens:
            keys.add(f"narration.{family}.{token}")
    keys.update({"narration.two_cups", "narration.profile", "narration.phase"})
    return frozenset(keys)


NARRATION_KEYS: Final[frozenset[str]] = _derive_narration_keys()
"""Every key all 29 locale files must carry — derived, never hand-listed."""


NARRATION_DRINK_PREFIX: Final = "narration.drink."
"""Key prefix of the optional spoken-drink-name family."""

NARRATION_DRINK_KEYS: Final[frozenset[str]] = frozenset({
    "americano", "americano_extra", "black_eye", "cafe_au_lait", "cafe_creme",
    "cafe_creme_doppio", "caffe_latte", "cappuccino", "cream", "dead_eye",
    "espresso", "espresso_doppio", "espresso_macchiato", "flat_white",
    "latte_macchiato", "latte_macchiato_extra", "latte_macchiato_triple",
    "long_black", "lungo", "red_eye", "ristretto", "ristretto_doppio",
})
"""The 22 recipe keys that may carry a SPOKEN drink name. **Optional per locale.**

Do not delete this family as "redundant with `recipes.name.*`". It is not, and
the reason is speech rather than display:

Of the 32 built-in recipe names, 10 are ordinary nouns that every locale really
translates (`hot_water`, `milk_froth`, `coffee`, …). The other 22 — the ones
listed here — are coffee proper names that house convention keeps in **Latin
script in every locale**, including the six that are not written in Latin script
at all (bg, el, mk, ru, sr, uk): ru's `recipes.name.cappuccino` is the string
`"Cappuccino"`. On a picker button that is correct and it stays. Read aloud by a
Russian or Greek TTS voice mid-sentence, the same string is a defect: the engine
either mispronounces the token or spells it out letter by letter.

So this family is a *pronunciation* override, consulted before `recipes.name.*`
and only on the speech path:

* `en` carries all 22 with the Latin values copied verbatim from
  `ui_strings/en.json`, so the family is an identity there. That keeps the
  parity rule "every locale's key set is a subset of en's" intact and lets the
  22 Latin-script locales omit the family entirely and still resolve — through
  the English overlay — to exactly the name their UI shows today.
* bg, el, mk, ru, sr and uk MUST carry all 22, in their own script.

The family is deliberately **not** part of `NARRATION_KEYS`: a missing override
must cost one word's pronunciation, never send the whole sentence to English the
way a missing mandatory key does (see `render`'s overlay guard)."""


def narration_keys() -> frozenset[str]:
    """The mandatory narration keyspace (imported by the asset test)."""
    return NARRATION_KEYS


def narration_drink_keys() -> frozenset[str]:
    """The 22 optional `narration.drink.*` recipe keys (bare, unprefixed)."""
    return NARRATION_DRINK_KEYS


def narration_all_keys() -> frozenset[str]:
    """Everything `narration_strings/en.json` carries: 45 mandatory + 22 optional."""
    return NARRATION_KEYS | {
        f"{NARRATION_DRINK_PREFIX}{key}" for key in NARRATION_DRINK_KEYS
    }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Narration:
    """One rendered sentence plus everything the caller must not guess.

    `language` is not cosmetic: an automation piping `description` into
    `tts.speak` has to pick a voice, and the English fallback must be visible to
    it. It always states the language the TEXT is actually in — never the
    language that was requested.
    """

    text: str | None
    """The sentence, or None when not even English could be rendered."""

    key: str | None
    """The head key that produced the sentence (`description_key`)."""

    language: str
    """The language of `text`; `""` when there is no text."""

    complete: bool
    """True when every fact the payload carried made it into the sentence."""

    missing_keys: tuple[str, ...]
    """Planned keys the string map did not have — for tests and diagnostics."""


NO_NARRATION: Final = Narration(
    text=None, key=None, language="", complete=False, missing_keys=(),
)
"""The "say nothing" result. The event still fires; `description` is absent."""


# ---------------------------------------------------------------------------
# Free text and substitution
# ---------------------------------------------------------------------------

_PH: Final = re.compile(r"\{([a-z_]{1,16})\}")

# Characters dropped from free text outright: markup, shell and format sigils
# that either read as noise through TTS or invite a second substitution pass.
# `{` and `}` are load-bearing here — a profile named "{drink}" must not be able
# to re-enter substitution (which is single-pass anyway; this is depth).
_DROP_CHARS: Final = frozenset('<>{}[]|\\^~`*_#@$%+=/;"')

# Stripped from both ends after cleaning, so "Anna," and "- Anna" both speak as
# "Anna" and a head template keeps sole ownership of sentence punctuation.
_EDGE_CHARS: Final = " .,:;!?-–—"

_MULTI_SPACE: Final = re.compile(r"  +")


def substitute(template: str, values: Mapping[str, Any]) -> str:
    """Fill `{name}` spans from `values`; unknown spans are removed.

    Deliberately not `str.format`: a stray `{` or a typo'd placeholder name in
    translator-supplied text raises `KeyError` / `ValueError` / `IndexError`,
    and this runs inside a BLE status callback. A residual literal brace is
    caught downstream by the render fallback instead of blowing up here.
    """
    if not isinstance(template, str):
        return ""
    return _PH.sub(lambda match: str(values.get(match.group(1), "")), template)


def _speakable(value: Any, limit: int = NARRATION_MAX_FREE_TEXT) -> str:
    """Reduce arbitrary text to something safe to speak, or `""`.

    Applied to user-authored names (machine profile registers, freestyle and
    sommelier recipe names) and to served labels. Letters, digits, spaces, `-`,
    `'`, `’` and `.` survive, so `Anna-Maria`, `O'Brien` and `Dr. Anna` are
    spoken as written; emoji, control characters and markup are not.

    An empty result is meaningful: it demotes the sentence to its `.unnamed`
    variant instead of leaving a dangling colon.
    """
    if not isinstance(value, str):
        return ""
    kept: list[str] = []
    for char in unicodedata.normalize("NFC", value):
        category = unicodedata.category(char)
        if category[0] == "C" or category in ("So", "Sk"):
            continue
        if char in _DROP_CHARS:
            continue
        kept.append(char)
    text = " ".join("".join(kept).split()).strip(_EDGE_CHARS)
    if limit and len(text) > limit:
        head = text[:limit]
        cut = head.rfind(" ")
        text = (head[:cut] if cut > 0 else head).strip(_EDGE_CHARS)
    if not any(char.isalnum() for char in text):
        return ""
    return text


def _volume(value: Any) -> int | None:
    """A component's `portion_ml` as a bare integer, or None if unusable.

    Consumes `portion_ml` only — never the raw wire portion byte, which counts
    in units of 5 ml and would narrate a fifth of the real volume.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        millilitres = int(round(value))
    except (OverflowError, ValueError):
        return None
    return millilitres


# ---------------------------------------------------------------------------
# Planning — pure, language-independent
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Clause:
    """One planned detail clause: its slot, its key and its substitutions."""

    slot: str
    key: str
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What a sentence needs, decided once and rendered in either language."""

    kind: str
    heads: tuple[str, ...]
    clauses: tuple[_Clause, ...]
    name_keys: tuple[str, ...]
    """**Bare** recipe keys (`"cappuccino"`), most specific first — not full
    string keys. `_resolve_drink` prefixes them, because one bare key is looked
    up in two different asset families (`narration.drink.*`, `recipes.name.*`)."""
    free_name: str
    prompt_key: str | None
    complete: bool
    shape_key: str | None = None
    """Full key of the shape sentence a *finished* brew may fall back to, or None.

    Deliberately NOT a member of `heads`: `heads` is what `render`'s overlay
    guard requires of a locale, and a locale missing its shape sentence must
    demote to its own `.unnamed` sentence, not switch the whole thing to
    English."""


def _components(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The payload's speakable components: mappings with a real process token."""
    raw = payload.get("components")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [
        item for item in raw
        if isinstance(item, Mapping)
        and item.get("process") not in NARRATION_OMITTED_TOKENS["process"]
        and item.get("process") is not None
    ]


def _component_clause(slot: str, component: Mapping[str, Any]) -> tuple[_Clause | None, bool]:
    """Build one component clause; the bool is False when a fact was lost."""
    token = component.get("process")
    if not isinstance(token, str) or token not in _COMPONENT_TOKENS:
        # New firmware, new process byte: drop this clause rather than invent a
        # word for it. The rest of the sentence stays grammatical.
        return None, False
    millilitres = _volume(component.get("portion_ml"))
    if millilitres is not None and millilitres > NARRATION_MAX_ML:
        return _Clause(slot, f"narration.component.{token}.plain"), False
    if millilitres is None or millilitres <= 0:
        return _Clause(slot, f"narration.component.{token}.plain"), True
    return (
        _Clause(
            slot,
            f"narration.component.{token}.with_amount",
            {"volume": str(millilitres)},
        ),
        True,
    )


def _value_token(
    components: Sequence[Mapping[str, Any]], family: str
) -> str | None:
    """The first non-default value of `family`, over the components it belongs to.

    A recipe carries these per component, but reading every component's value
    aloud is a mouthful, so the first one that states something other than the
    machine default wins. Deterministic, and it matches how the machine actually
    applies them.

    The candidate set is narrowed first: a `_COFFEE_ONLY_SLOTS` family is read
    only from a `process == "coffee"` component. Scanning positionally instead
    used to narrate a milk-first latte macchiato — whose FIRST component is the
    milk one, carrying a filler `"medium"` — as "medium strength" no matter how
    strong the coffee really was, and gave a water- or milk-only drink a
    strength clause when it has no strength at all.
    """
    omitted = NARRATION_OMITTED_TOKENS.get(family, frozenset())
    if family in _COFFEE_ONLY_SLOTS:
        components = [
            component for component in components
            if component.get("process") == _COFFEE_PROCESS
        ]
    for component in components:
        token = component.get(family)
        if isinstance(token, str) and token and token not in omitted:
            return token
    return None


def _phase_clause(payload: Mapping[str, Any]) -> _Clause | None:
    """`pour 2 of 3`, but only for a genuinely multi-phase sommelier drink."""
    index = payload.get("phase_index")
    total = payload.get("phase_total")
    if isinstance(index, bool) or isinstance(total, bool):
        return None
    if not isinstance(index, int) or not isinstance(total, int):
        return None
    if total < 2 or index < 0 or index >= total:
        return None
    return _Clause("phase", "narration.phase", {"n": str(index + 1), "m": str(total)})


def _plan_brew(payload: Mapping[str, Any], kind: str) -> _Plan:
    """Plan a brew sentence: the drink, then the detail clauses in SLOT_ORDER.

    `shape` is planned for `brew_finished` alone, and only as a *head* candidate:
    the detector puts the token in the payload whenever it could classify the
    brew — a named drink included — but a sentence that said both would read
    "Ready: cappuccino — a milk coffee", which is redundant and sounds broken.
    `_compose` therefore consults it only when there is no drink name to speak.
    """
    named, unnamed, named_detail, unnamed_detail = _BREW_HEADS[kind]
    heads = tuple(key for key in (named, unnamed, named_detail, unnamed_detail) if key)
    complete = True
    clauses: list[_Clause] = []

    if named_detail is not None:
        phase = _phase_clause(payload)
        if phase is not None:
            clauses.append(phase)

        components = _components(payload)
        for slot, component in zip(("component1", "component2"), components):
            clause, kept = _component_clause(slot, component)
            complete = complete and kept
            if clause is not None:
                clauses.append(clause)
        if len(components) > 2:
            complete = False

        for family in _VALUE_SLOTS:
            token = _value_token(components, family)
            if token is None:
                continue
            key = f"narration.{family}.{token}"
            if key not in NARRATION_KEYS:
                complete = False
                continue
            clauses.append(_Clause(family, key))

        if payload.get("two_cups") is True:
            clauses.append(_Clause("two_cups", "narration.two_cups"))

        profile = _speakable(payload.get("profile_name"))
        if profile:
            clauses.append(
                _Clause("profile", "narration.profile", {"profile": profile})
            )

        clauses.sort(key=lambda clause: SLOT_ORDER.index(clause.slot))
        if len(clauses) > NARRATION_MAX_CLAUSES:
            clauses = clauses[:NARRATION_MAX_CLAUSES]
            complete = False

    shape = payload.get("shape")
    shape_key = (
        _SHAPE_KEYS.get(shape)
        if kind == EVENT_BREW_FINISHED and isinstance(shape, str)
        else None
    )

    recipe_key = payload.get("recipe_key")
    name_keys: list[str] = []
    if isinstance(recipe_key, str) and recipe_key:
        name_keys.append(recipe_key)
        mapped = DIRECTKEY_NAME_KEYS.get(recipe_key)
        if mapped and mapped != recipe_key:
            name_keys.append(mapped)

    return _Plan(
        kind=kind,
        heads=heads,
        clauses=tuple(clauses),
        name_keys=tuple(name_keys),
        free_name=_speakable(payload.get("recipe_name")),
        prompt_key=None,
        complete=complete,
        shape_key=shape_key,
    )


def _plan(payload: Mapping[str, Any], kind: str) -> _Plan | None:
    """Decide which keys this sentence needs. Pure and language-independent."""
    if kind in _BREW_HEADS:
        return _plan_brew(payload, kind)

    if kind == EVENT_PROMPT_RAISED:
        token = payload.get("prompt")
        prompt_key: str | None = None
        if isinstance(token, str) and token and token != _MANIPULATION_NONE:
            prompt_key = f"status.manipulation.{token}"
        return _Plan(
            kind=kind,
            heads=(_PROMPT_RAISED_KEY, _PROMPT_GENERIC_KEY),
            clauses=(),
            name_keys=(),
            free_name="",
            prompt_key=prompt_key,
            complete=True,
        )

    if kind == EVENT_PROMPT_CLEARED:
        return _Plan(kind, (_PROMPT_CLEARED_KEY,), (), (), "", None, True)

    if kind == EVENT_MAINTENANCE_FINISHED:
        token = payload.get("process")
        if not isinstance(token, str) or token not in _MAINTENANCE_TOKENS:
            # An unmapped or non-maintenance process has no authored sentence
            # and must not be templated into one.
            return None
        return _Plan(
            kind,
            (f"narration.event.maintenance_finished.{token}",),
            (), (), "", None, True,
        )

    return None


# ---------------------------------------------------------------------------
# Composition — one pass per language
# ---------------------------------------------------------------------------

def _template(strings: Mapping[str, str], key: str) -> str | None:
    """The string at `key`, or None when it is absent, not a string, or blank."""
    value = strings.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _resolve_drink(
    plan: _Plan,
    *,
    narration_strings: Mapping[str, str],
    narration_en: Mapping[str, str],
    ui_strings: Mapping[str, str],
) -> str:
    """The drink name for `{drink}`, or `""` (which demotes the head variant).

    Four steps, tried in order for each bare key in `plan.name_keys` (the
    payload's `recipe_key` first, then the `DIRECTKEY_NAME_KEYS` mapping of it),
    and then the free-text name:

    1. `narration.drink.<key>` in **this locale's** narration file — the spoken
       form, when the locale ships one;
    2. `narration.drink.<key>` in `narration_strings/en.json` — the English
       overlay, which is where the 22 Latin-script locales that omit the family
       land, and which yields exactly the Latin name their UI shows;
    3. `recipes.name.<key>` in the en-overlaid served `ui_strings` map — the
       locale's own UI translation. This is the only step the 10 genuinely
       translated names ever need (ru `Горячая вода`, `Молочная пенка`), since
       they carry no override;
    4. the sanitised free-text `recipe_name` (sommelier and freestyle drinks),
       and finally `""`.

    Why step 1 and 2 exist at all — do not "simplify" them away
    ----------------------------------------------------------
    Steps 3 and 4 alone were the earlier design, on the reasoning that
    `ui_strings` already carries all 32 recipe names in all 29 locales. That is
    true and it is still wrong for **speech**: 22 of those 32 names are coffee
    proper names that house convention deliberately keeps in Latin script in
    every locale, so step 3 hands a Cyrillic or Greek TTS voice the token
    `"Cappuccino"`, which it mispronounces or spells out. The override family
    (see `NARRATION_DRINK_KEYS`) exists purely to give the speech path a
    pronounceable form without touching a single served `recipes.name.*` string,
    which remains the correct label on every picker button.

    A missing override is *not* a gap: it never triggers the whole-sentence
    English fallback, it just falls to the next step of this chain.
    """
    for key in plan.name_keys:
        for candidate in (
            narration_strings.get(f"{NARRATION_DRINK_PREFIX}{key}"),
            narration_en.get(f"{NARRATION_DRINK_PREFIX}{key}"),
            ui_strings.get(f"recipes.name.{key}"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                spoken = _speakable(candidate)
                if spoken:
                    return spoken
    return plan.free_name


def _compose(
    plan: _Plan,
    *,
    strings: Mapping[str, str],
    narration_en: Mapping[str, str],
    ui_strings: Mapping[str, str],
    language: str,
) -> Narration:
    """Render `plan` in one language. Returns `NO_NARRATION` if it cannot.

    `narration_en` is here only for `_resolve_drink`'s step 2: the spoken-name
    override falls back per key to English, unlike every other narration key,
    which falls back as a whole sentence.
    """
    missing: list[str] = []
    complete = plan.complete

    clauses: list[str] = []
    for clause in plan.clauses:
        template = strings.get(clause.key)
        if not isinstance(template, str) or not template.strip():
            missing.append(clause.key)
            complete = False
            continue
        text = substitute(template, clause.values).strip()
        if not text:
            complete = False
            continue
        clauses.append(text)
    detail = NARRATION_SEPARATOR.join(clauses)

    values: dict[str, Any] = {"detail": detail}
    if plan.kind in BREW_KINDS:
        drink = _resolve_drink(
            plan,
            narration_strings=strings,
            narration_en=narration_en,
            ui_strings=ui_strings,
        )
        values["drink"] = drink
        named, unnamed, named_detail, unnamed_detail = _BREW_HEADS[plan.kind]
        # Variant selection happens AFTER slot resolution, never before: an
        # empty drink demotes to `.unnamed*` and an empty clause list demotes
        # away from `.*_detail`. That ordering is structurally why ", , " and
        # dangling separators are impossible — punctuation only ever lives in a
        # template chosen in the knowledge that its slots are full.
        if drink and detail and named_detail:
            head_key = named_detail
        elif drink:
            head_key = named
        elif detail and unnamed_detail:
            head_key = unnamed_detail
        elif plan.shape_key is not None:
            # Nothing is known about the drink but its shape — the front-panel
            # case. Resolved per key against THIS language's map rather than
            # through the overlay guard: a locale that is missing the sentence
            # keeps speaking its own language and says `.unnamed`, which is
            # exactly what it said before shapes existed.
            if _template(strings, plan.shape_key) is not None:
                head_key = plan.shape_key
            else:
                head_key = unnamed
                missing.append(plan.shape_key)
                complete = False
        else:
            head_key = unnamed
    elif plan.kind == EVENT_PROMPT_RAISED:
        label = ""
        if plan.prompt_key:
            label = _speakable(ui_strings.get(plan.prompt_key), NARRATION_MAX_LABEL)
        values["prompt"] = label
        head_key = _PROMPT_RAISED_KEY if label else _PROMPT_GENERIC_KEY
    else:
        head_key = plan.heads[0]

    template = _template(strings, head_key)
    if template is None:
        return NO_NARRATION

    text = _MULTI_SPACE.sub(" ", substitute(template, values)).strip()
    if not text or "{" in text or "}" in text:
        # A translator left a literal brace, or the template is blank: the
        # sentence is unusable in this language.
        return NO_NARRATION
    return Narration(
        text=text,
        key=head_key,
        language=language,
        complete=complete,
        missing_keys=tuple(missing),
    )


def _event_kind(payload: Mapping[str, Any], event_type: str | None) -> str | None:
    """The lifecycle event type, from the argument or from the payload itself."""
    for candidate in (event_type, payload.get("type"), payload.get("kind")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def render(
    payload: Mapping[str, Any],
    *,
    event_type: str | None = None,
    locale: str = "en",
    narration: Mapping[str, str] | None = None,
    narration_en: Mapping[str, str] | None = None,
    ui_strings: Mapping[str, str] | None = None,
    ui_strings_en: Mapping[str, str] | None = None,
) -> Narration:
    """Render one lifecycle event as a sentence. Never raises.

    `payload` is the §2.4 event payload; every field in it may be absent. The
    event type comes from `event_type`, or from the payload's own `type` /
    `kind` key when the caller passes the two together.

    The four string maps, and why there are four:

    * `narration` — the **raw** resolved `narration_strings/<locale>.json`, with
      no English merge. The overlay guard has to be able to see the gaps.
    * `narration_en` — the raw `narration_strings/en.json`.
    * `ui_strings` — the en-overlaid merged `ui_strings` map for the same
      resolved locale (`recipes.name.*`, `status.manipulation.*`).
    * `ui_strings_en` — the merged map for `"en"`.

    Any of them may be empty; a cold cache simply yields `NO_NARRATION` and the
    event fires without a `description`.

    Fallback chain:

    1. every planned key present in the locale file -> the locale sentence;
    2. any planned key absent -> the **whole** sentence in English,
       `language="en"`, `complete=False`. Never a code-switched sentence: half a
       Russian sentence in English is the worst i18n bug class there is, and it
       is structurally impossible here;
    3. the drink name resolves on its own four-step chain, which is the one
       deliberate per-key exception to level 2: `narration.drink.<key>` in the
       locale -> `narration.drink.<key>` in English -> `recipes.name.<key>` in
       the merged ui_strings map -> the free-text name (see `_resolve_drink`).
       The override family is optional per locale, so its absence never sends
       the sentence to English — a locale without it simply speaks the Latin
       name it already shows in the UI;
    4. a drink that is not a built-in -> the sanitised free-text name; empty
       demotes to the `.unnamed` head — or, on a `brew_finished` whose payload
       carries a `shape`, to that shape's own sentence, which is looked up per
       key in the language being rendered and demotes to `.unnamed` in that same
       language when it is missing, never to English;
    5. an unknown value token -> that one clause is dropped, `complete=False`;
    6. a template that renders blank or keeps a literal brace -> one English
       retry;
    7. English broken too, or the string cache cold -> `text=None`.

    Level 7 is the point of the whole design: narration must never be able to
    stop an event from firing.
    """
    try:
        # Referenced, never copied: this runs on the BLE callback path and the
        # ui_strings maps are ~300 entries each.
        locale_strings = narration if isinstance(narration, Mapping) else {}
        en_strings = narration_en if isinstance(narration_en, Mapping) else {}
        locale_ui = ui_strings if isinstance(ui_strings, Mapping) else {}
        en_ui = ui_strings_en if isinstance(ui_strings_en, Mapping) else {}
        language = locale if isinstance(locale, str) and locale else "en"

        kind = _event_kind(payload, event_type)
        if kind is None:
            return NO_NARRATION
        plan = _plan(payload, kind)
        if plan is None:
            return NO_NARRATION

        planned = set(plan.heads) | {clause.key for clause in plan.clauses}
        # The overlay guard. A locale that is missing even one planned key is
        # rendered wholly in English rather than clause by clause: a sentence
        # that switches language halfway through is the worse bug, and it is
        # the one this check makes structurally impossible.
        gaps = sorted(key for key in planned if key not in locale_strings)
        if not gaps:
            result = _compose(
                plan,
                strings=locale_strings,
                narration_en=en_strings,
                ui_strings=locale_ui,
                language=language,
            )
            if result.text is not None:
                return result

        result = _compose(
            plan,
            strings=en_strings,
            narration_en=en_strings,
            ui_strings=en_ui,
            language="en",
        )
        if result.text is None:
            return NO_NARRATION
        if language == "en":
            return result
        # A locale that reached English did so because something was missing.
        return Narration(
            text=result.text,
            key=result.key,
            language="en",
            complete=False,
            missing_keys=tuple(gaps) or result.missing_keys,
        )
    except Exception:  # noqa: BLE001 - a narration bug must never eat an event
        _LOGGER.debug("Narration rendering failed", exc_info=True)
        return NO_NARRATION


# ---------------------------------------------------------------------------
# Asset loading — EXECUTOR ONLY below this line. Nothing above it does I/O.
# ---------------------------------------------------------------------------

NARRATION_STRINGS_DIR: Final = pathlib.Path(__file__).parent / "narration_strings"
"""Where the 29 narration asset files live. Read through the module global at
call time so a test can point it somewhere else."""

_LOCALE_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,35}$")


def _read_narration_file(locale: str) -> dict[str, str] | None:
    """Read one `narration_strings/<locale>.json`, or None if unusable.

    Executor-only (blocking file I/O). A missing file is the normal "locale not
    shipped" signal; a malformed one is logged and treated the same, so a bad
    asset can only ever cost the sentence, never the event.
    """
    path = NARRATION_STRINGS_DIR / f"{locale}.json"
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _LOGGER.warning("Unreadable narration asset: %s", path)
        return None
    if not isinstance(data, dict):
        _LOGGER.warning("Narration asset is not a flat map: %s", path)
        return None
    return {str(key): value for key, value in data.items() if isinstance(value, str)}


def load_narration_strings(
    requested_locale: str,
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve a locale against `narration_strings/` -> (resolved, map, en map).

    Executor-only (blocking file I/O). The resolution chain mirrors `ui_strings`:
    exact match -> base language (`de-DE` -> `de`) -> `en`. A missing or
    unusable file resolves to `en`.

    Unlike the `ui_strings` loader this returns the **raw** resolved file with no
    English merge, because `render`'s overlay guard has to be able to see which
    keys the locale is missing — merging them away would reintroduce exactly the
    silent mid-sentence code-switching the guard exists to prevent.
    """
    candidates: list[str] = []
    if isinstance(requested_locale, str) and _LOCALE_RE.match(requested_locale):
        candidates.append(requested_locale)
        base = re.split(r"[-_]", requested_locale, maxsplit=1)[0].lower()
        if base and base not in candidates:
            candidates.append(base)

    en_map = _read_narration_file("en") or {}
    for candidate in candidates:
        if candidate == "en":
            break
        locale_map = _read_narration_file(candidate)
        if locale_map is not None:
            return candidate, locale_map, en_map
    return "en", dict(en_map), en_map


def directkey_category_tokens() -> frozenset[str]:
    """The live DirectKey category tokens `DIRECTKEY_NAME_KEYS` must cover."""
    return frozenset(category.name.lower() for category in DirectKeyCategory)
