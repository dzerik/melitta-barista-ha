"""AI recipe generation for Coffee Sommelier using HA conversation agents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

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

# ── Valid extras ────────────────────────────────────────────────────────

VALID_SYRUPS = {
    "vanilla", "caramel", "hazelnut", "chocolate", "maple",
    "lavender", "pumpkin_spice", "coconut", "almond", "peppermint",
}
VALID_TOPPINGS = {
    "cinnamon_powder", "whipped_cream", "cocoa_powder", "nutmeg",
    "chocolate_shavings", "marshmallow", "caramel_drizzle",
}
VALID_LIQUEURS = {
    "baileys", "kahlua", "amaretto", "frangelico", "grand_marnier",
}

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
) -> str:
    """Build structured prompt for the LLM.

    `intro` is the user-editable persona/instruction prefix
    (`sommelier_intro` slot). When None we fall back to the bundled default.
    The dynamic context (beans, milk, time-of-day, weather, etc.) is always
    appended. Set `omit_output_format=True` when the caller is about to
    auto-append a JSON-Schema block via panel_api._structured_call; the
    legacy text Output Format spec is included only for the (deprecated)
    direct-conversation path.
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

    # Bean descriptions
    hopper_section = ""
    if hopper1_bean:
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

    if hopper2_bean:
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
    cup_section = (
        f"## Cup Size\n"
        f"Cup type: {cup_size} ({vol_min}-{vol_max}ml total volume).\n"
        f"Total volume (component1 + component2) must fit within {vol_min}-{vol_max}ml."
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
        extras_section = (
            "\n## Available Extras\n"
            + "\n".join(extras_lines)
            + '\n\nInclude relevant extras in the "extras" field of each recipe.'
        )

    # Temperature preference
    temp_pref_section = ""
    temperature_pref = temperature_pref if temperature_pref in VALID_TEMPERATURE_PREFS else "auto"
    if temperature_pref == "iced":
        temp_pref_section = "\n## Temperature: iced\nPrefer cold/iced drinks."
    elif temperature_pref == "hot":
        temp_pref_section = "\n## Temperature: hot\nPrefer hot drinks only."

    # Weather section
    weather_section = ""
    if weather:
        temp_c = weather.get("temperature")
        condition = weather.get("condition", "")
        if temp_c is not None:
            weather_section = f"\n## Weather\nCurrent: {temp_c}\u00b0C, {condition}"
            if isinstance(temp_c, (int, float)):
                if temp_c <= 10:
                    weather_section += "\n-> Suggest warming, comforting drinks."
                elif temp_c >= 25:
                    weather_section += "\n-> Suggest iced/cold refreshing drinks."

    # Mood / Occasion
    mood_section = ""
    if mood and mood in VALID_MOODS:
        mood_section = f"\n## Mood: {mood}"
    occasion_section = ""
    if occasion and occasion in VALID_OCCASIONS:
        occasion_section = f"\n## Occasion: {occasion}"

    # Dietary
    dietary_section = ""
    if dietary:
        valid = [d for d in dietary if d in VALID_DIETARY]
        if valid:
            dietary_section = (
                f"\n## Dietary restrictions: {', '.join(valid)}\n"
                "-> Respect these constraints: "
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
        caffeine_section = "\n## Caffeine preference: low\n-> Use fewer shots, milder intensity."
    elif caffeine_pref == "decaf_evening":
        caffeine_section = (
            "\n## Caffeine preference: decaf_evening\n"
            "Current time is evening -> suggest decaf/low-caffeine options."
        )

    # Servings
    servings_section = ""
    if servings > 1:
        servings_section = (
            f"\n## Servings: {servings}"
            + (f" (for {people_home} people)" if people_home else "")
            + "\nGenerate diverse recipes with different styles."
        )

    # Cups today
    cups_section = ""
    if cups_today is not None and cups_today > 0:
        cups_section = (
            f"\n## Coffee today: already had {cups_today} cups\n"
            "Consider reducing caffeine. Suggest milk-based or decaf."
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
    ]))

    intro_text = (intro or _DEFAULT_INTRO)
    try:
        intro_text = intro_text.format(count=count, mode=mode)
    except (KeyError, IndexError):
        # User template uses placeholders we don't supply — pass through
        # literally so they can spot the mismatch in the LLM reply.
        pass

    output_format_block = "" if omit_output_format else _OUTPUT_FORMAT_BLOCK

    return f"""{intro_text}

## Machine Capabilities
Each recipe has up to 2 components (dispensed sequentially). Each component has:
- process: "coffee", "milk", or "water" (component 2 can also be "none" to disable)
- intensity: "very_mild", "mild", "medium", "strong", "very_strong" (coffee strength)
- aroma: "standard" or "intense" (grind fineness — intense = finer grind, more extraction)
- temperature: "cold", "normal", "high"
- shots: "none", "one", "two", "three" (espresso shots — only meaningful for coffee process)
- portion_ml: 5 to 250, in steps of 5 (volume in milliliters)

The "blend" field selects which bean hopper to use (see below).

## Available Beans
{hopper_section}
## Milk
{milk_section}

## Context
- Time of day: {time_desc}
- {time_advice}
- {pref_section}

{optional_sections}

## Rules
- Realistic portion sizes: espresso 25-40ml, lungo 100-150ml, americano 150-200ml, milk portion 80-200ml
- Match bean characteristics to recipe style (light roast -> standard aroma, dark roast -> intense aroma)
- If milk is available, include at least one milk-based recipe (unless user prefers black)
- For "none" process in component2, set all other fields to defaults (intensity="medium", aroma="standard", temperature="normal", shots="none", portion_ml=0)
- Each recipe MUST have a creative name and a 1-2 sentence description explaining the taste profile
- If two hoppers available, use both across the recipe set
- blend field: 1 for hopper 1 beans, 0 for hopper 2 beans. If only one hopper, always use that one.
{output_format_block}"""


_OUTPUT_FORMAT_BLOCK = """
## Output Format
Return ONLY a JSON array, no other text:
[
  {
    "name": "Recipe Name",
    "description": "Tasting notes and why this works",
    "blend": 1,
    "component1": {"process": "coffee", "intensity": "strong", "aroma": "intense", "temperature": "normal", "shots": "two", "portion_ml": 30},
    "component2": {"process": "milk", "intensity": "medium", "aroma": "standard", "temperature": "high", "shots": "none", "portion_ml": 120},
    "extras": {"ice": false, "syrup": "caramel", "topping": "cinnamon_powder", "liqueur": null, "instruction": "Optional human instruction for extras"},
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
    """Validate and normalize a recipe component."""
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

    syrup = raw_extras.get("syrup")
    if isinstance(syrup, str):
        syrup = syrup.lower()
        if syrup not in VALID_SYRUPS:
            syrup = None
    else:
        syrup = None

    topping = raw_extras.get("topping")
    if isinstance(topping, str):
        topping = topping.lower()
        if topping not in VALID_TOPPINGS:
            topping = None
    else:
        topping = None

    liqueur = raw_extras.get("liqueur")
    if isinstance(liqueur, str):
        liqueur = liqueur.lower()
        if liqueur not in VALID_LIQUEURS:
            liqueur = None
    else:
        liqueur = None

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
    """Validate and normalize a list of recipes from LLM."""
    validated = []
    for raw in raw_recipes:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "AI Recipe"))[:100]
        description = str(raw.get("description", ""))[:500]

        blend = raw.get("blend", 1)
        if blend not in (0, 1):
            blend = 1

        comp1 = _validate_component(raw.get("component1", {}), is_comp2=False)
        comp2 = _validate_component(raw.get("component2", {}), is_comp2=True)

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

        recipe: dict[str, Any] = {
            "name": name,
            "description": description,
            "blend": blend,
            "component1": comp1,
            "component2": comp2,
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
) -> list[dict[str, Any]]:
    """Generate freestyle recipes using HA conversation agent."""
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
