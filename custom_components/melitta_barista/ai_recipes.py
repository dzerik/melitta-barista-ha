"""AI recipe generation for Coffee Sommelier using HA conversation agents."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("melitta_barista")

# ── Valid freestyle parameter values ──────────────────────────────────

VALID_PROCESSES = {"coffee", "milk", "water", "none"}
VALID_INTENSITIES = {"very_mild", "mild", "medium", "strong", "very_strong"}
VALID_AROMAS = {"standard", "intense"}
VALID_TEMPERATURES = {"cold", "normal", "high"}
VALID_SHOTS = {"none", "one", "two", "three"}

PORTION_MIN = 5
PORTION_MAX = 250
PORTION_STEP = 5


def _build_prompt(
    hopper1_bean: dict[str, Any] | None,
    hopper2_bean: dict[str, Any] | None,
    milk_types: list[str],
    mode: str,
    preference: str | None,
    count: int,
) -> str:
    """Build structured prompt for the LLM."""
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

    return f"""You are an expert barista and coffee sommelier. Generate exactly {count} unique coffee recipes for a Melitta Barista Smart coffee machine.

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

## Rules
- Realistic portion sizes: espresso 25-40ml, lungo 100-150ml, americano 150-200ml, milk portion 80-200ml
- Match bean characteristics to recipe style (light roast → standard aroma, dark roast → intense aroma)
- If milk is available, include at least one milk-based recipe (unless user prefers black)
- For "none" process in component2, set all other fields to defaults (intensity="medium", aroma="standard", temperature="normal", shots="none", portion_ml=0)
- Each recipe MUST have a creative name and a 1-2 sentence description explaining the taste profile
- If two hoppers available, use both across the recipe set
- blend field: 1 for hopper 1 beans, 0 for hopper 2 beans. If only one hopper, always use that one.

## Output Format
Return ONLY a JSON array, no other text:
[
  {{
    "name": "Recipe Name",
    "description": "Tasting notes and why this works",
    "blend": 1,
    "component1": {{"process": "coffee", "intensity": "strong", "aroma": "intense", "temperature": "normal", "shots": "two", "portion_ml": 30}},
    "component2": {{"process": "milk", "intensity": "medium", "aroma": "standard", "temperature": "high", "shots": "none", "portion_ml": 120}}
  }}
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

        validated.append({
            "name": name,
            "description": description,
            "blend": blend,
            "component1": comp1,
            "component2": comp2,
        })

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
) -> list[dict[str, Any]]:
    """Generate freestyle recipes using HA conversation agent."""
    prompt = _build_prompt(
        hopper1_bean=hopper1_bean,
        hopper2_bean=hopper2_bean,
        milk_types=milk_types,
        mode=mode,
        preference=preference,
        count=count,
    )

    _LOGGER.debug("Sommelier prompt: %s", prompt[:200])

    # Call conversation.process service
    service_data: dict[str, Any] = {"text": prompt}
    if llm_agent:
        service_data["agent_id"] = llm_agent

    try:
        response = await hass.services.async_call(
            "conversation",
            "process",
            service_data,
            blocking=True,
            return_response=True,
        )
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
