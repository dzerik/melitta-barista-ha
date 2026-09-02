"""AI recipe generation for Coffee Sommelier using HA conversation agents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .capabilities import LiveCapabilities

_LOGGER = logging.getLogger("melitta_barista")

LLM_TIMEOUT = 60.0

# ── Valid freestyle parameter values ──────────────────────────────────

VALID_PROCESSES = {"coffee", "milk", "water", "none"}
VALID_INTENSITIES = {"very_mild", "mild", "medium", "strong", "very_strong"}
VALID_AROMAS = {"standard", "intense"}
VALID_TEMPERATURES = {"cold", "normal", "high"}
VALID_SHOTS = {"none", "one", "two", "three"}

PORTION_MIN = 5
PORTION_MAX = 250
PORTION_STEP = 5

CUP_SIZE_VOLUMES: dict[str, tuple[int, int]] = {
    "espresso_cup": (60, 90),
    "cup": (150, 200),
    "mug": (250, 350),
    "tall_glass": (300, 400),
    "travel": (350, 500),
}
VALID_CUP_SIZES = set(CUP_SIZE_VOLUMES.keys())

VALID_TEMPERATURE_PREFS = {"hot", "iced", "auto"}
VALID_MOODS = {"energizing", "relaxing", "dessert", "classic"}
VALID_OCCASIONS = {"morning", "after_lunch", "guests", "romantic", "work"}
VALID_CAFFEINE_PREFS = {"regular", "low", "decaf_evening"}
VALID_DIETARY = {"no_sugar", "lactose_free", "low_calorie", "vegan"}


_DEFAULT_INTRO = (
    "You are an expert barista and coffee sommelier. Generate exactly {count} "
    "unique coffee recipes for a bean-to-cup smart coffee machine."
)

# Hard cap on the anti-repeat "Existing Recipes" prompt section. Local
# LLMs pay for every prompt token during prefill (documented lesson from
# the configurable-timeout work), so the section stays terse: at most
# this many one-line entries.
EXISTING_RECIPES_CAP = 12


def _recency_str(created_at: Any, now: datetime | None = None) -> str | None:
    """Human recency phrase for a stored ISO timestamp.

    Returns "today", "yesterday" or "N days ago" relative to `now`
    (default: current UTC time); None when the timestamp is missing or
    unparseable so callers can simply omit the recency fragment.
    """
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(str(created_at))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    days = (now - dt).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _summarize_recipe(rec: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Reduce a stored recipe/favorite row to a compact anti-repeat summary.

    Output keys: `name`, `milk` (bool — any milk machine phase), `strength`
    (first coffee phase intensity or None), `blend` (raw LLM semantics,
    1 = hopper 1 / 0 = hopper 2), `extras` (list of short strings like
    "vanilla syrup", "ice") and `recency` (see `_recency_str`).
    """
    phases = rec.get("machine_phases") or []
    comps = [p.get("component") or {} for p in phases if isinstance(p, dict)]
    has_milk = any(c.get("process") == "milk" for c in comps)
    strength = None
    for c in comps:
        if c.get("process") == "coffee":
            strength = c.get("intensity")
            break
    extras = rec.get("extras") or {}
    extra_bits: list[str] = []
    if isinstance(extras, dict):
        for kind in ("syrup", "topping", "liqueur"):
            value = extras.get(kind)
            if value:
                extra_bits.append(f"{value} {kind}")
        if extras.get("ice"):
            extra_bits.append("ice")
    return {
        "name": str(rec.get("name", "")).strip(),
        "milk": has_milk,
        "strength": strength,
        "blend": rec.get("blend"),
        "extras": extra_bits,
        "recency": _recency_str(rec.get("created_at"), now=now),
    }


async def _existing_recipe_summaries(
    db: Any,
    *,
    machine_profile: int | None = None,
    history_sessions: int = 10,
    max_history_recipes: int = 10,
) -> list[dict[str, Any]]:
    """Collect anti-repeat summaries of what the user already has.

    Shared helper for every generation call site: fetches favorites
    (`db.async_list_favorites`) plus the most recent history recipes
    (`db.async_list_history`, newest sessions first, flattened and
    truncated to `max_history_recipes`), both scoped with the same
    `machine_profile` filter semantics the endpoints use (profile rows +
    shared NULL rows; None = everything). Entries are deduplicated by
    case-insensitive name, favorites first, and shaped by
    `_summarize_recipe`. The prompt-side cap is `EXISTING_RECIPES_CAP`;
    this helper only bounds the history fetch.
    """
    now = datetime.now(timezone.utc)
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _push(rec: dict[str, Any]) -> None:
        summary = _summarize_recipe(rec, now=now)
        key = summary["name"].lower()
        if not key or key in seen:
            return
        seen.add(key)
        summaries.append(summary)

    for fav in await db.async_list_favorites(machine_profile_filter=machine_profile):
        _push(fav)

    sessions = await db.async_list_history(
        limit=history_sessions, machine_profile_filter=machine_profile
    )
    recent: list[dict[str, Any]] = []
    for sess in sessions:
        for rec in sess.get("recipes") or []:
            if not isinstance(rec, dict):
                continue
            if not rec.get("created_at"):
                rec = {**rec, "created_at": sess.get("created_at")}
            recent.append(rec)
    for rec in recent[:max_history_recipes]:
        _push(rec)

    return summaries


def _build_prompt(
    hopper1_bean: dict[str, Any] | None,
    hopper2_bean: dict[str, Any] | None,
    milk_types: list[str],
    mode: str,
    preference: str | None,
    count: int,
    *,
    extras: dict[str, list[str]] | None = None,
    ice_available: bool = False,
    cup_size: str = "mug",
    temperature_pref: str = "auto",
    mood: str | None = None,
    occasion: str | None = None,
    servings: int = 1,
    dietary: list[str] | None = None,
    caffeine_pref: str = "regular",
    weather: dict[str, Any] | None = None,
    people_home: int | None = None,
    cups_today: int | None = None,
    intro: str | None = None,
    omit_output_format: bool = False,
    language: str | None = None,
    moods: list[str] | None = None,
    caps: LiveCapabilities | None = None,
    existing_recipes: list[dict[str, Any]] | None = None,
    compact: bool = False,
) -> str:
    """Build structured prompt for the LLM.

    `intro` is the user-editable persona/instruction prefix
    (`sommelier_intro` slot). When None we fall back to the bundled default.
    The dynamic context (beans, milk, time-of-day, weather, etc.) is always
    appended. Set `omit_output_format=True` when the caller is about to
    auto-append a JSON-Schema block via panel_api._structured_call; the
    legacy text Output Format spec is included only for the (deprecated)
    direct-conversation path.

    `existing_recipes` is the anti-repeat context: compact summaries from
    `_existing_recipe_summaries` (favorites + recent history). When
    non-empty, an "Existing Recipes" section tells the LLM not to repeat
    them — one terse line per entry, capped at `EXISTING_RECIPES_CAP`.

    `compact=True` (settings key `compact_prompt`, issue #38 follow-up)
    emits a significantly shorter prompt for local LLMs, where prefill
    time is proportional to prompt length. It preserves everything
    correctness-critical — bean names/roast plus up to 3 flavor notes,
    milk and extras inventories, machine capability enums and portion
    limits, cup volume bounds, dietary constraints, the anti-repeat
    recipe names (recency dropped) and the schema contract (steps/phase/
    reasoning fields, enums-stay-English) — while condensing guidance
    prose (time-of-day advice, weather/mood commentary, phase-tagging
    explanations, rules) to single lines. The response must satisfy the
    same JSON schema either way.
    """
    now = datetime.now(timezone.utc)
    hour = now.hour

    if 5 <= hour < 12:
        time_desc = f"morning ({hour:02d}:00 UTC)"
        time_advice = "Morning: prefer stronger, energizing drinks. Espresso-based with moderate portions."
    elif 12 <= hour < 17:
        time_desc = f"afternoon ({hour:02d}:00 UTC)"
        time_advice = "Afternoon: balanced drinks, medium strength. Good for milk-based recipes."
    elif 17 <= hour < 21:
        time_desc = f"evening ({hour:02d}:00 UTC)"
        time_advice = "Evening: lighter, milder drinks. Lower caffeine, smaller portions or milk-heavy."
    else:
        time_desc = f"night ({hour:02d}:00 UTC)"
        time_advice = "Night: very mild or decaf-style. Small portions, warm milk drinks."

    if compact:
        # One short line: keep only the first sentence of the advice.
        time_advice = time_advice.split(". ")[0].rstrip(".") + "."

    # Bean descriptions
    hopper_section = ""
    if compact:
        # Terse one-liners: name + roast + up to 3 flavor notes.
        for hopper_no, blend, bean in ((1, 1, hopper1_bean), (2, 0, hopper2_bean)):
            if not bean:
                continue
            line = (
                f"- Hopper {hopper_no} (blend={blend}): "
                f"{bean['brand']} {bean['product']}, {bean['roast']} roast"
            )
            notes = list(bean.get("flavor_notes", []))[:3]
            if notes:
                line += f"; notes: {', '.join(notes)}"
            hopper_section += line + "\n"
    elif hopper1_bean:
        notes = ", ".join(hopper1_bean.get("flavor_notes", []))
        hopper_section += (
            f"- Hopper 1 (blend=1): {hopper1_bean['brand']} {hopper1_bean['product']}\n"
            f"  Roast: {hopper1_bean['roast']}, Type: {hopper1_bean['bean_type']}, "
            f"Origin: {hopper1_bean['origin']}"
        )
        if hopper1_bean.get("origin_country"):
            hopper_section += f" ({hopper1_bean['origin_country']})"
        if notes:
            hopper_section += f"\n  Flavor notes: {notes}"
        if hopper1_bean.get("composition"):
            hopper_section += f"\n  Composition: {hopper1_bean['composition']}"
        hopper_section += "\n"

    if not compact and hopper2_bean:
        notes = ", ".join(hopper2_bean.get("flavor_notes", []))
        hopper_section += (
            f"- Hopper 2 (blend=0): {hopper2_bean['brand']} {hopper2_bean['product']}\n"
            f"  Roast: {hopper2_bean['roast']}, Type: {hopper2_bean['bean_type']}, "
            f"Origin: {hopper2_bean['origin']}"
        )
        if hopper2_bean.get("origin_country"):
            hopper_section += f" ({hopper2_bean['origin_country']})"
        if notes:
            hopper_section += f"\n  Flavor notes: {notes}"
        if hopper2_bean.get("composition"):
            hopper_section += f"\n  Composition: {hopper2_bean['composition']}"
        hopper_section += "\n"

    if not hopper_section:
        hopper_section = "- No beans configured. Use generic coffee parameters.\n"

    # Milk
    if milk_types:
        milk_section = f"Available milk: {', '.join(milk_types)}"
    else:
        milk_section = "No milk available. Generate black coffee recipes only."

    # Preference
    if mode == "surprise_me":
        pref_section = "Mode: SURPRISE ME — be creative and diverse! Include different styles."
    elif preference:
        pref_section = f'Mode: custom. User preference: "{preference}"'
    else:
        pref_section = "Mode: custom. No specific preference given."

    # Cup size / volume constraints
    cup_size = cup_size if cup_size in VALID_CUP_SIZES else "mug"
    vol_min, vol_max = CUP_SIZE_VOLUMES[cup_size]
    if compact:
        cup_section = (
            f"Cup: {cup_size} — sum of machine_phases portion_ml must fit "
            f"{vol_min}-{vol_max}ml."
        )
    else:
        cup_section = (
            f"## Cup Size\n"
            f"Cup type: {cup_size} ({vol_min}-{vol_max}ml total volume).\n"
            f"Total volume (sum of machine_phases portion_ml) must fit within {vol_min}-{vol_max}ml."
        )

    # Extras section
    extras_lines: list[str] = []
    if extras:
        for category, items in extras.items():
            if items:
                extras_lines.append(f"- {category.capitalize()}: {', '.join(items)}")
    if ice_available:
        extras_lines.append("- Ice: available")
    extras_section = ""
    if extras_lines:
        if compact:
            extras_section = (
                "\n## Available Extras\n"
                + "\n".join(extras_lines)
                + '\nUse relevant ones in "extras".'
            )
        else:
            extras_section = (
                "\n## Available Extras\n"
                + "\n".join(extras_lines)
                + '\n\nInclude relevant extras in the "extras" field of each recipe.'
            )

    # Temperature preference
    temp_pref_section = ""
    temperature_pref = temperature_pref if temperature_pref in VALID_TEMPERATURE_PREFS else "auto"
    if temperature_pref == "iced":
        temp_pref_section = (
            "\nTemperature: iced \u2014 prefer cold/iced drinks." if compact
            else "\n## Temperature: iced\nPrefer cold/iced drinks."
        )
    elif temperature_pref == "hot":
        temp_pref_section = (
            "\nTemperature: hot \u2014 prefer hot drinks only." if compact
            else "\n## Temperature: hot\nPrefer hot drinks only."
        )

    # Weather section
    weather_section = ""
    if weather:
        temp_c = weather.get("temperature")
        condition = weather.get("condition", "")
        if temp_c is not None:
            if compact:
                weather_section = f"\nWeather: {temp_c}\u00b0C, {condition}."
            else:
                weather_section = f"\n## Weather\nCurrent: {temp_c}\u00b0C, {condition}"
                if isinstance(temp_c, (int, float)):
                    if temp_c <= 10:
                        weather_section += "\n-> Suggest warming, comforting drinks."
                    elif temp_c >= 25:
                        weather_section += "\n-> Suggest iced/cold refreshing drinks."

    # Mood / Occasion. Multi-mood (`moods` list) wins over single `mood`.
    mood_section = ""
    valid_moods = [m for m in (moods or []) if m in VALID_MOODS]
    if valid_moods:
        if compact:
            mood_section = f"\nMoods: {', '.join(valid_moods)}."
        else:
            mood_section = (
                f"\n## Moods: {', '.join(valid_moods)}\n"
                "Generate recipes that satisfy at least one of these moods; "
                "spread variety across the moods if more than one is asked for."
            )
    elif mood and mood in VALID_MOODS:
        mood_section = f"\nMood: {mood}." if compact else f"\n## Mood: {mood}"
    occasion_section = ""
    if occasion and occasion in VALID_OCCASIONS:
        occasion_section = (
            f"\nOccasion: {occasion}." if compact else f"\n## Occasion: {occasion}"
        )

    # Dietary
    dietary_section = ""
    if dietary:
        valid = [d for d in dietary if d in VALID_DIETARY]
        if valid:
            # Dietary is a hard constraint — kept verbatim in compact mode,
            # just without the section header.
            dietary_section = (
                f"\nDietary restrictions: {', '.join(valid)} — " if compact
                else (
                    f"\n## Dietary restrictions: {', '.join(valid)}\n"
                    "-> Respect these constraints: "
                )
            )
            hints: list[str] = []
            if "lactose_free" in valid or "vegan" in valid:
                hints.append("use plant-based milk only")
            if "no_sugar" in valid:
                hints.append("no sugar syrups")
            if "low_calorie" in valid:
                hints.append("minimize calorie-dense extras")
            dietary_section += ", ".join(hints) + "." if hints else "follow these restrictions."

    # Caffeine preference
    caffeine_section = ""
    caffeine_pref = caffeine_pref if caffeine_pref in VALID_CAFFEINE_PREFS else "regular"
    if caffeine_pref == "low":
        caffeine_section = (
            "\nCaffeine: low — fewer shots, milder." if compact
            else "\n## Caffeine preference: low\n-> Use fewer shots, milder intensity."
        )
    elif caffeine_pref == "decaf_evening":
        caffeine_section = (
            "\nCaffeine: decaf_evening — prefer decaf now." if compact
            else (
                "\n## Caffeine preference: decaf_evening\n"
                "Current time is evening -> suggest decaf/low-caffeine options."
            )
        )

    # Servings
    servings_section = ""
    if servings > 1:
        if compact:
            servings_section = (
                f"\nServings: {servings}"
                + (f" (for {people_home} people)" if people_home else "")
                + "."
            )
        else:
            servings_section = (
                f"\n## Servings: {servings}"
                + (f" (for {people_home} people)" if people_home else "")
                + "\nGenerate diverse recipes with different styles."
            )

    # Cups today
    cups_section = ""
    if cups_today is not None and cups_today > 0:
        if compact:
            cups_section = f"\nCoffee today: {cups_today} cups — go easier on caffeine."
        else:
            cups_section = (
                f"\n## Coffee today: already had {cups_today} cups\n"
                "Consider reducing caffeine. Suggest milk-based or decaf."
            )

    # Anti-repeat section — field report: without it the LLM happily
    # re-suggests recipes the user already saved. One line per entry,
    # capped, so local-LLM prefill stays cheap.
    existing_section = ""
    if existing_recipes:
        existing_lines: list[str] = []
        for er in existing_recipes[:EXISTING_RECIPES_CAP]:
            name = str(er.get("name", "")).strip()
            if not name:
                continue
            traits: list[str] = []
            milk = er.get("milk")
            if isinstance(milk, str) and milk:
                traits.append(f"{milk} milk")
            else:
                traits.append("milk" if milk else "no milk")
            er_extras = er.get("extras") or []
            if er_extras:
                traits.append("extras: " + ", ".join(str(x) for x in er_extras))
            blend = er.get("blend")
            if blend in (0, 1):
                traits.append("hopper 1" if blend == 1 else "hopper 2")
            strength = er.get("strength")
            if strength:
                traits.append(f"strength: {strength}")
            recency = er.get("recency")
            if recency and not compact:
                # Recency phrases are advisory flavor — dropped in
                # compact mode; the names are what prevent duplicates.
                traits.append(recency)
            existing_lines.append(f'- "{name}" — {", ".join(traits)}')
        if existing_lines:
            existing_section = (
                "\n## Existing Recipes\n"
                "The user already has these recipes — do NOT repeat them; "
                "propose something meaningfully different:\n"
                + "\n".join(existing_lines)
            )

    # Combine optional sections
    optional_sections = "".join(filter(None, [
        cup_section,
        extras_section,
        temp_pref_section,
        weather_section,
        mood_section,
        occasion_section,
        dietary_section,
        caffeine_section,
        servings_section,
        cups_section,
        existing_section,
    ]))

    intro_text = (intro or _DEFAULT_INTRO)
    try:
        intro_text = intro_text.format(count=count, mode=mode)
    except (KeyError, IndexError):
        # User template uses placeholders we don't supply — pass through
        # literally so they can spot the mismatch in the LLM reply.
        pass

    output_format_block = "" if omit_output_format else _OUTPUT_FORMAT_BLOCK

    # Locale section — uses HA's `hass.config.language` so the names,
    # descriptions, ingredient names and step instructions come back in
    # the user's UI language. The schema (JSON Schema field names,
    # enum values like "intense" / "arabica") stays English so it
    # validates regardless of locale.
    language_section = ""
    if language:
        if compact:
            language_section = (
                f"\nLanguage: {language} for human-readable strings "
                f"(name, description, reasoning, steps); keep enum values "
                f"in English so they validate."
            )
        else:
            language_section = (
                f"\n## Language\n"
                f"User locale: {language}. Reply with all human-readable strings "
                f"(name, description, reasoning, step.action, step.ingredient, step.notes, "
                f"extras.instruction) in this language. Keep enum values "
                f"(roast, intensity, processes, units like \"ml\") in English "
                f"so they validate against the schema."
            )

    # Steps section — explicit instruction to fill `steps` with the full
    # preparation sequence and dosages, not just the machine portion.
    # NEW: LLM must tag each step with a phase (pre/during/post).
    if compact:
        steps_section = (
            "\n## Preparation steps\n"
            "Fill `steps` with the full preparation sequence in order. Tag "
            "each step's `phase`: \"pre\" (manual, before machine), "
            "\"during\" (machine/concurrent; default), \"post\" (manual, "
            "after). Set `amount` + `unit` for quantities, null otherwise."
        )
    else:
        steps_section = (
            "\n## Preparation steps\n"
            "Populate `steps` with the COMPLETE preparation sequence the user "
            "must follow, in execution order. **Tag each step with its phase:**\n"
            "\n"
            "- `\"phase\": \"pre\"` — manual preparation BEFORE the machine starts "
            "(selecting the cup, adding ice, scooping cocoa, measuring sugar).\n"
            "- `\"phase\": \"during\"` — machine action OR a manual step that runs "
            "concurrently with the brew (the machine command itself, or "
            "\"hold the cup at 45°\"). This is the default.\n"
            "- `\"phase\": \"post\"` — manual finalization AFTER the machine finishes "
            "(topping with whipped cream, dusting, garnishing, stirring).\n"
            "\n"
            "Each step must carry an `amount` + `unit` when there is a quantity "
            "(\"15\" + \"ml\", \"1\" + \"scoop\"); use null when the action is "
            "purely instructional (\"Stir for 10 seconds\")."
        )

    def _fmt_enum(values):
        return ", ".join(f'"{v}"' for v in values)

    if caps is not None:
        processes_str = _fmt_enum(caps.supported_processes)
        intensities_str = _fmt_enum(caps.supported_intensities)
        aromas_str = _fmt_enum(caps.supported_aromas)
        temperatures_str = _fmt_enum(caps.supported_temperatures)
        shots_str = _fmt_enum(caps.supported_shots)
        if caps.portion_limits:
            mins = [pl["min"] for pl in caps.portion_limits.values()]
            maxs = [pl["max"] for pl in caps.portion_limits.values()]
            steps = [pl["step"] for pl in caps.portion_limits.values()]
            portion_min, portion_max = min(mins), max(maxs)
            portion_step = max(steps) if steps else 5
        else:
            portion_min, portion_max, portion_step = 0, 250, 5
        if compact:
            capabilities_block = (
                f"## Machine Capabilities (this machine: {caps.model_name})\n"
                f"Use ONLY the values listed here; anything else fails the brew:\n"
                f"- process: one of {processes_str}\n"
                f"- intensity: one of {intensities_str}\n"
                f"- aroma: one of {aromas_str}\n"
                f"- temperature: one of {temperatures_str}\n"
                f"- shots: one of {shots_str}\n"
                f"- portion_ml: integer from {portion_min} to {portion_max} in steps of {portion_step}\n"
                f"`blend` selects the bean hopper (see below)."
            )
        else:
            capabilities_block = (
                f"## Machine Capabilities (this machine: {caps.model_name})\n"
                f"\n"
                f"The machine accepts these per-component parameters. "
                f"Use only the values listed below. Ignore any other values that "
                f"the response JSON schema may technically permit — they are NOT "
                f"supported by this machine and selecting them will produce a brew failure.\n"
                f"\n"
                f"- process: one of {processes_str}\n"
                f"- intensity: one of {intensities_str}\n"
                f"- aroma: one of {aromas_str}\n"
                f"- temperature: one of {temperatures_str}\n"
                f"- shots: one of {shots_str}\n"
                f"- portion_ml: integer from {portion_min} to {portion_max} in steps of {portion_step}\n"
                f"\n"
                f"The \"blend\" field selects which bean hopper to use (see below)."
            )
    elif compact:
        capabilities_block = (
            '## Machine Capabilities\n'
            'Each recipe: 1-2 machine_phases (dispensed sequentially), each with a `component`:\n'
            '- process: "coffee", "milk", or "water"\n'
            '- intensity: "very_mild", "mild", "medium", "strong", "very_strong"\n'
            '- aroma: "standard" or "intense"\n'
            '- temperature: "cold", "normal", "high"\n'
            '- shots: "none", "one", "two", "three"\n'
            '- portion_ml: 5 to 250, step 5\n'
            '`blend` selects the bean hopper (see below).'
        )
    else:
        capabilities_block = (
            '## Machine Capabilities\n'
            'Each recipe specifies 1 or 2 machine_phases (dispensed sequentially). Each phase has a `component` with:\n'
            '- process: "coffee", "milk", or "water"\n'
            '- intensity: "very_mild", "mild", "medium", "strong", "very_strong" (coffee strength)\n'
            '- aroma: "standard" or "intense" (grind fineness — intense = finer grind, more extraction)\n'
            '- temperature: "cold", "normal", "high"\n'
            '- shots: "none", "one", "two", "three" (espresso shots — only meaningful for coffee process)\n'
            '- portion_ml: 5 to 250, in steps of 5 (volume in milliliters)\n'
            '\n'
            'The "blend" field selects which bean hopper to use (see below).'
        )

    if compact:
        rules_block = (
            "## Rules\n"
            "- Realistic portions: espresso 25-40ml, lungo 100-150ml, americano 150-200ml, milk 80-200ml\n"
            "- Use 1 machine_phase by default; max 2 (only for layered drinks)\n"
            "- Each recipe needs a creative name, a 1-2 sentence description, and a one-sentence `reasoning` in the user's language tied to the current context\n"
            "- If two hoppers available, use both across the set\n"
            "- blend: 1 = hopper 1, 0 = hopper 2; with one hopper always use it"
        )
    else:
        rules_block = (
            "## Rules\n"
            "- Realistic portion sizes: espresso 25-40ml, lungo 100-150ml, americano 150-200ml, milk portion 80-200ml\n"
            "- Match bean characteristics to recipe style (light roast -> standard aroma, dark roast -> intense aroma)\n"
            "- If milk is available, include at least one milk-based recipe (unless user prefers black)\n"
            "- Use 1 machine_phase by default. Add a 2nd machine_phase only when a single-phase brew can't achieve the result — e.g. layered drinks, milk added cold after espresso. NEVER use more than 2 phases.\n"
            "- Each recipe MUST have a creative name and a 1-2 sentence description explaining the taste profile\n"
            "- Each recipe MUST include a \"reasoning\" field: ONE sentence, in the user's language, explaining why THIS pick fits the current context — link it to the stated mood, occasion, weather, time of day, and/or the available beans\n"
            "- If two hoppers available, use both across the recipe set\n"
            "- blend field: 1 for hopper 1 beans, 0 for hopper 2 beans. If only one hopper, always use that one."
        )

    return f"""{intro_text}

{capabilities_block}

## Available Beans
{hopper_section}
## Milk
{milk_section}

## Context
- Time of day: {time_desc}
- {time_advice}
- {pref_section}

{optional_sections}{language_section}{steps_section}

{rules_block}
{output_format_block}"""


_OUTPUT_FORMAT_BLOCK = """
## Output Format
Return ONLY a JSON array, no other text:
[
  {
    "name": "Latte",
    "description": "Classic milk-forward coffee",
    "reasoning": "A gentle milk-forward pick for this rainy afternoon that lets your dark-roast beans shine.",
    "blend": 1,
    "machine_phases": [
      {
        "component": {"process": "coffee", "intensity": "medium", "aroma": "standard", "temperature": "normal", "shots": "two", "portion_ml": 40},
        "user_action_before": []
      },
      {
        "component": {"process": "milk", "intensity": "medium", "aroma": "standard", "temperature": "normal", "shots": "none", "portion_ml": 160},
        "user_action_before": [{"order": 1, "action": "Place a 240ml cup under the spout"}]
      }
    ],
    "steps": [
      {"order": 1, "phase": "pre", "action": "Take a 240ml ceramic mug"},
      {"order": 2, "phase": "pre", "action": "Add ice cubes", "amount": 3, "unit": "cubes"},
      {"order": 3, "phase": "during", "action": "Brew espresso shot"},
      {"order": 4, "phase": "post", "action": "Top with cold milk", "amount": 80, "unit": "ml"},
      {"order": 5, "phase": "post", "action": "Dust with cinnamon"}
    ],
    "extras": {"ice": false, "syrup": null, "topping": null, "liqueur": null, "instruction": null},
    "cup_type": "mug",
    "estimated_caffeine": "medium",
    "calories_approx": 120
  }
]"""


def _extract_json(text: str) -> list[dict[str, Any]]:
    """Extract JSON array from LLM response text."""
    # Try direct parse
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Regex fallback: find array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON array from LLM response: {text[:200]}...")


def _clamp_portion(value: Any) -> int:
    """Clamp and round portion_ml to valid range."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 40  # default
    v = max(0, min(v, PORTION_MAX))
    v = round(v / PORTION_STEP) * PORTION_STEP
    return max(v, 0)


def _validate_component(comp: dict[str, Any], is_comp2: bool = False) -> dict[str, Any]:
    """Validate and normalize a recipe component.

    ``is_comp2=True`` keeps the legacy second-component semantics: an
    unknown process degrades to "none" (meaning "no phase") rather than
    to "coffee". Callers validating machine_phases pass ``idx > 0``.
    """
    process = str(comp.get("process", "coffee")).lower()
    if is_comp2 and process not in VALID_PROCESSES:
        process = "none"
    elif not is_comp2 and process not in {"coffee", "milk", "water"}:
        process = "coffee"

    if process == "none":
        return {
            "process": "none",
            "intensity": "medium",
            "aroma": "standard",
            "temperature": "normal",
            "shots": "none",
            "portion_ml": 0,
        }

    intensity = str(comp.get("intensity", "medium")).lower()
    if intensity not in VALID_INTENSITIES:
        intensity = "medium"

    aroma = str(comp.get("aroma", "standard")).lower()
    if aroma not in VALID_AROMAS:
        aroma = "standard"

    temperature = str(comp.get("temperature", "normal")).lower()
    if temperature not in VALID_TEMPERATURES:
        temperature = "normal"

    shots = str(comp.get("shots", "none")).lower()
    if shots not in VALID_SHOTS:
        shots = "one" if process == "coffee" else "none"

    portion_ml = _clamp_portion(comp.get("portion_ml", 40 if process == "coffee" else 100))
    if not is_comp2 and portion_ml < PORTION_MIN:
        portion_ml = PORTION_MIN

    return {
        "process": process,
        "intensity": intensity,
        "aroma": aroma,
        "temperature": temperature,
        "shots": shots,
        "portion_ml": portion_ml,
    }


def _validate_extras(raw_extras: Any) -> dict[str, Any] | None:
    """Validate and normalize the extras field from LLM response."""
    if not isinstance(raw_extras, dict):
        return None

    ice = bool(raw_extras.get("ice", False))

    # Free-form strings: the LLM is told which extras the user actually
    # configured (under Add-ins) in the prompt, and Pydantic accepts any
    # string for these fields. The old allowlist below silently dropped
    # any user-configured syrup/topping/liqueur the LLM correctly used,
    # because VALID_* is a small hardcoded English set.
    def _clean(v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        cleaned = v.strip().lower()
        return cleaned[:64] if cleaned else None

    syrup = _clean(raw_extras.get("syrup"))
    topping = _clean(raw_extras.get("topping"))
    liqueur = _clean(raw_extras.get("liqueur"))

    instruction = raw_extras.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        instruction = None
    else:
        instruction = instruction.strip()[:200]

    # If everything is empty/default, return None
    if not ice and syrup is None and topping is None and liqueur is None and instruction is None:
        return None

    return {
        "ice": ice,
        "syrup": syrup,
        "topping": topping,
        "liqueur": liqueur,
        "instruction": instruction,
    }


def _validate_recipes(raw_recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize a list of recipes from LLM.

    Clamps portions/enums to machine-accepted values, passes `reasoning`
    and `steps` through, and drops placeholder "none" machine phases.
    """
    validated = []
    for raw in raw_recipes:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "AI Recipe"))[:100]
        description = str(raw.get("description", ""))[:500]
        # "Why this recipe?" — the LLM's one-sentence justification tying
        # the pick to mood/occasion/weather/beans (rendered by the panel's
        # collapsible Why? expander).
        reasoning = str(raw.get("reasoning") or "")[:500]

        blend = raw.get("blend", 1)
        if blend not in (0, 1):
            blend = 1

        # P2a: validate machine_phases (list of 1..2 phases) instead of component1/2.
        raw_phases = raw.get("machine_phases") or []
        if not isinstance(raw_phases, list) or not raw_phases:
            raw_phases = [{"component": {}}]
        if len(raw_phases) > 2:
            raw_phases = raw_phases[:2]
        # A phase whose process is "none" is a placeholder ("no phase"),
        # not a brewable step — drop it instead of coercing it into a
        # phantom 40ml coffee pour. Phase index > 0 keeps the legacy
        # second-component semantics (unknown process -> "none" -> drop).
        machine_phases = []
        for idx, p in enumerate(raw_phases):
            if not isinstance(p, dict):
                continue
            raw_comp = p.get("component") or {}
            if str(raw_comp.get("process", "")).lower() == "none":
                continue
            component = _validate_component(raw_comp, is_comp2=idx > 0)
            if component["process"] == "none":
                continue
            machine_phases.append({
                "component": component,
                "user_action_before": p.get("user_action_before") or [],
            })
        if not machine_phases:
            # Degenerate all-"none" recipe: keep the min-1-phase invariant
            # with a default coffee phase (matches the pre-P2a fallback).
            machine_phases = [{
                "component": _validate_component({}),
                "user_action_before": [],
            }]

        extras = _validate_extras(raw.get("extras"))

        cup_type = str(raw.get("cup_type", "mug")).lower()
        if cup_type not in VALID_CUP_SIZES:
            cup_type = "mug"

        estimated_caffeine = str(raw.get("estimated_caffeine", "medium")).lower()
        if estimated_caffeine not in {"low", "medium", "high", "none"}:
            estimated_caffeine = "medium"

        calories_approx: int | None = None
        raw_cal = raw.get("calories_approx")
        if raw_cal is not None:
            try:
                calories_approx = max(0, int(raw_cal))
            except (TypeError, ValueError):
                calories_approx = None

        # Pass through `steps` from the LLM. Pydantic in panel_api has
        # already enforced the per-step shape (order/action/dosage); this
        # validator only needs to keep the field on the dict so it
        # reaches the UI and the favourites store.
        steps = raw.get("steps")
        if not isinstance(steps, list):
            steps = []

        recipe: dict[str, Any] = {
            "name": name,
            "description": description,
            "reasoning": reasoning,
            "blend": blend,
            "machine_phases": machine_phases,
            "steps": steps,
            "extras": extras,
            "cup_type": cup_type,
            "estimated_caffeine": estimated_caffeine,
            "calories_approx": calories_approx,
        }

        validated.append(recipe)

    return validated


async def async_generate_recipes(
    hass: HomeAssistant,
    hopper1_bean: dict[str, Any] | None,
    hopper2_bean: dict[str, Any] | None,
    milk_types: list[str],
    mode: str,
    preference: str | None,
    count: int,
    llm_agent: str | None,
    *,
    extras: dict[str, list[str]] | None = None,
    ice_available: bool = False,
    cup_size: str = "mug",
    temperature_pref: str = "auto",
    mood: str | None = None,
    occasion: str | None = None,
    servings: int = 1,
    dietary: list[str] | None = None,
    caffeine_pref: str = "regular",
    weather: dict[str, Any] | None = None,
    people_home: int | None = None,
    cups_today: int | None = None,
    intro: str | None = None,
    language: str | None = None,
    moods: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Generate freestyle recipes using HA conversation agent.

    .. deprecated::
        Legacy direct-conversation path, no longer used by the live
        Sommelier pipeline — `sommelier_api.ws_generate` calls
        `panel_api._structured_call` instead (schema-validated, with its
        own LLM_TIMEOUT bound). Kept for backwards compatibility only.
    """
    prompt = _build_prompt(
        hopper1_bean=hopper1_bean,
        hopper2_bean=hopper2_bean,
        milk_types=milk_types,
        mode=mode,
        preference=preference,
        count=count,
        extras=extras,
        ice_available=ice_available,
        cup_size=cup_size,
        temperature_pref=temperature_pref,
        intro=intro,
        mood=mood,
        occasion=occasion,
        servings=servings,
        dietary=dietary,
        caffeine_pref=caffeine_pref,
        weather=weather,
        people_home=people_home,
        cups_today=cups_today,
        language=language,
        moods=moods,
    )

    _LOGGER.debug("Sommelier prompt: %s", prompt[:200])

    # Call conversation.process service
    service_data: dict[str, Any] = {"text": prompt}
    if llm_agent:
        service_data["agent_id"] = llm_agent

    try:
        response = await asyncio.wait_for(
            hass.services.async_call(
                "conversation",
                "process",
                service_data,
                blocking=True,
                return_response=True,
            ),
            timeout=LLM_TIMEOUT,
        )
    except asyncio.TimeoutError as err:
        raise RuntimeError(
            f"LLM request timed out after {LLM_TIMEOUT:.0f}s. "
            "The conversation agent did not respond in time."
        ) from err
    except Exception as err:
        raise RuntimeError(
            f"Failed to call conversation.process: {err}. "
            "Make sure a conversation agent (e.g. OpenAI, Anthropic, Google) "
            "is configured in Home Assistant."
        ) from err

    # Extract speech text from response
    if not response or not isinstance(response, dict):
        raise RuntimeError(f"Empty response from conversation agent: {response}")

    speech = (
        response.get("response", {})
        .get("speech", {})
        .get("plain", {})
        .get("speech", "")
    )

    if not speech:
        raise RuntimeError(f"No speech in conversation response: {response}")

    _LOGGER.debug("Sommelier LLM response: %s", speech[:200])

    # Parse and validate
    raw_recipes = _extract_json(speech)
    validated = _validate_recipes(raw_recipes)

    if not validated:
        raise RuntimeError(
            f"LLM returned no valid recipes. Raw response: {speech[:300]}"
        )

    _LOGGER.info("Sommelier generated %d recipes", len(validated))
    return validated
