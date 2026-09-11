"""Guards for the docs that GitHub and HACS render to the public.

`hacs.json` sets `render_readme: true`, so `README.md` *is* the project's front
page, and everything under `docs/` that is committed is published to the docs
site. Three classes of defect are pinned here, all of them found by reading the
rendered page rather than the code:

1. **Provenance wording.** The tree deliberately keeps its protocol-analysis
   notes untracked; the committed docs must not carry the vocabulary those
   notes use. The forbidden fragments below are assembled from pieces on
   purpose, so this guard does not itself put the phrase into the repository.
2. **Copy-pasteable examples.** Every YAML block in the README is something a
   user pastes verbatim, so the values it compares against are asserted against
   the code that produces them — a state condition against the state sensor's
   real label, the narration sentences against the real renderer output.
3. **Claims about defaults and floors.** The documented default of an import
   flag is asserted against the shipped default in the panel and in the
   WebSocket schema; the documented Home Assistant floor is asserted against
   `hacs.json` and against the automation schema the examples actually use.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from custom_components.melitta_barista.const import DOMAIN, MachineProcess
from custom_components.melitta_barista.narration import render
from custom_components.melitta_barista.sensor import PROCESS_LABELS

REPO = Path(__file__).parent.parent
COMPONENT_DIR = REPO / "custom_components" / "melitta_barista"
README = REPO / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse every run of whitespace, so a hard-wrapped sentence matches."""
    return re.sub(r"\s+", " ", text)


# ---------------------------------------------------------------------------
# 1. Provenance wording in the published docs
# ---------------------------------------------------------------------------

def _tracked_markdown() -> list[Path]:
    """Every committed Markdown file, i.e. exactly what the public can read."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "*.md"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        pytest.skip("git unavailable; cannot enumerate the tracked docs")
    return [REPO / line for line in out.splitlines() if line]


# Assembled from fragments so the guard does not spell the phrase out in the
# tree it is guarding.
_FORBIDDEN = (
    "".join(("re", "verse-engineer")),
    "".join(("re", "verse engineer")),
    "".join(("de", "compil")),
    "".join(("ap", "k")),
    "".join(("ja", "dx")),
    "".join(("ils", "pycmd")),
    "".join(("mobile", "app")),
)


def test_tracked_markdown_carries_no_provenance_vocabulary():
    """No committed doc describes the protocol work in the forbidden terms."""
    offenders: list[str] = []
    for path in _tracked_markdown():
        lowered = _read(path).lower()
        for fragment in _FORBIDDEN:
            if fragment in lowered:
                for number, line in enumerate(lowered.splitlines(), start=1):
                    if fragment in line:
                        rel = path.relative_to(REPO)
                        offenders.append(f"{rel}:{number}: {fragment!r}")
    assert offenders == [], "public docs carry forbidden wording:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# 2. The automation examples
# ---------------------------------------------------------------------------

def _yaml_block(heading: str) -> dict:
    """The first fenced YAML block below a README heading, parsed."""
    text = _read(README)
    start = text.index(heading)
    fence = text.index("```yaml", start) + len("```yaml")
    end = text.index("```", fence)
    return yaml.safe_load(text[fence:end])


def test_morning_espresso_condition_matches_the_state_sensor():
    """The example's state condition is the label the sensor actually reports.

    Home Assistant compares state conditions case-sensitively, so a lowercase
    `ready` here would make the pasted automation silently never fire.
    """
    block = _yaml_block("### Morning Espresso")
    condition = block["automation"][0]["conditions"][0]
    assert condition["entity_id"] == "sensor.melitta_state"
    assert condition["state"] == PROCESS_LABELS[MachineProcess.READY]


def test_readme_process_token_hint_matches_the_enum():
    """The documented machine-readable alternative uses the real token name."""
    flat = _flat(_read(README))
    assert "'process_token'" in flat
    assert f"== '{MachineProcess.READY.name}'" in flat


# ---------------------------------------------------------------------------
# 3. The narration examples
# ---------------------------------------------------------------------------

# The drink the README advertises: a cappuccino, 40 ml of coffee and 160 ml of
# hot milk, strong — the payload shape `event.py` publishes.
README_CAPPUCCINO = {
    "source": "ha",
    "recipe_source": "directkey",
    "recipe_key": "cappuccino",
    "recipe_name": "Cappuccino",
    "two_cups": False,
    "components": [
        {"process": "coffee", "intensity": "strong", "aroma": "standard",
         "temperature": "normal", "shots": "none", "portion_ml": 40},
        {"process": "milk", "intensity": "medium", "aroma": "standard",
         "temperature": "normal", "shots": "none", "portion_ml": 160},
    ],
    "total_ml": 200,
    "final": True,
}


def _assets(locale: str) -> tuple[dict, dict]:
    narration = json.loads(_read(COMPONENT_DIR / "narration_strings" / f"{locale}.json"))
    ui_en = json.loads(_read(COMPONENT_DIR / "ui_strings" / "en.json"))
    if locale == "en":
        return narration, ui_en
    ui = {**ui_en, **json.loads(_read(COMPONENT_DIR / "ui_strings" / f"{locale}.json"))}
    return narration, ui


@pytest.mark.parametrize("locale", ["en", "ru", "de"])
def test_readme_narration_examples_are_verbatim_renderer_output(locale):
    """Each advertised sentence is what `render()` produces, word for word.

    ru is the load-bearing case: `narration.drink.cappuccino` is "капучино", so
    the Russian sentence never contains the Latin token the picker shows.
    """
    narration_map, ui_map = _assets(locale)
    narration_en, ui_en = _assets("en")
    result = render(
        README_CAPPUCCINO,
        event_type="brew_finished",
        locale=locale,
        narration=narration_map,
        narration_en=narration_en,
        ui_strings=ui_map,
        ui_strings_en=ui_en,
    )
    assert result.complete is True
    assert _flat(result.text) in _flat(_read(README))


def test_readme_does_not_advertise_a_latin_drink_name_in_russian():
    """The Cyrillic examples never quote the Latin token the renderer replaces."""
    for quoted in re.findall(r"«([^»]*)»", _flat(_read(README))):
        if "Сварено" in quoted or "Готовлю" in quoted:
            assert "Cappuccino" not in quoted, quoted


# ---------------------------------------------------------------------------
# 4. The Home Assistant floor
# ---------------------------------------------------------------------------

_MODERN_SCHEMA_FLOOR = (2024, 10)


def _floor(text: str) -> tuple[int, int]:
    match = re.search(r"\*\*Home Assistant\*\* (\d{4})\.(\d{1,2})", text)
    assert match is not None, "README no longer states a Home Assistant floor"
    return int(match.group(1)), int(match.group(2))


def test_readme_floor_matches_hacs_json():
    """The prose floor, the badge and the HACS manifest all say one number."""
    readme = _read(README)
    major, minor = _floor(readme)
    hacs = json.loads(_read(REPO / "hacs.json"))["homeassistant"]
    assert hacs.startswith(f"{major}.{minor}.")
    assert f"HA-{major}.{minor}%2B" in readme


def test_readme_floor_covers_the_automation_schema_it_uses():
    """Modern-schema examples below the floor must carry the rename note.

    The integration's floor is set by the core APIs it calls, not by the YAML
    dialect the examples happen to be written in: raising the declared floor to
    match the prettier schema would drop working installs out of HACS for a
    documentation preference. So when the examples use `triggers:`/`actions:`
    while the declared floor predates 2024.10, the README must tell readers on
    an older core how to rename the blocks.
    """
    readme = _read(README)
    assert "\n    triggers:\n" in readme, "no modern-schema example left to guard"
    if _floor(readme) < _MODERN_SCHEMA_FLOOR:
        major, minor = _MODERN_SCHEMA_FLOOR
        assert f"**{major}.{minor}**" in readme, "no core-version note for the schema"
        assert "classic" in readme and "`trigger:`" in readme, (
            "the schema note no longer explains the rename for an older core"
        )


# ---------------------------------------------------------------------------
# 5. The documented import-flag default
# ---------------------------------------------------------------------------

def _shipped_install_specific_default() -> bool:
    panel = _read(COMPONENT_DIR / "www" / "components" / "melitta-settings.js")
    js = re.search(r"this\._includeInstallSpecific\s*=\s*(true|false)\s*;", panel)
    assert js is not None, "panel no longer initialises the import flag"
    api = _read(COMPONENT_DIR / "sommelier_api.py")
    schema = re.search(
        r'vol\.Optional\("include_install_specific",\s*default=(True|False)\)', api
    )
    assert schema is not None, "WS schema no longer defaults the import flag"
    assert (js.group(1) == "true") == (schema.group(1) == "True"), (
        "panel and WebSocket schema disagree on the shipped default"
    )
    return js.group(1) == "true"


def test_readme_states_the_shipped_import_flag_default():
    """The flag table names the state the checkbox actually ships in."""
    row = next(
        line
        for line in _read(README).splitlines()
        if line.startswith("| **Take LLM agent and weather entity from the backup**")
    )
    expected = "**On by default**" if _shipped_install_specific_default() else "**Off by default**"
    assert expected in row


# ---------------------------------------------------------------------------
# 6. The automation examples explain how to actually use them
# ---------------------------------------------------------------------------

def test_configuration_yaml_examples_warn_about_the_ui_editor():
    """`automation:`-wrapped examples must say they are not editor-ready.

    The examples are written in `configuration.yaml` form, which is what a
    reader pastes into the automation editor's YAML mode first — where the
    `automation:` key is rejected outright. Shipping the wrapper without the
    warning turns every example into a support question.
    """
    readme = _read(README)
    if "\nautomation:\n" not in readme:
        return
    assert "extra keys not allowed" in readme, "no UI-editor warning for the wrapper"
    assert "start at `alias:`" in readme, "the warning no longer says what to do instead"


def test_device_id_placeholder_is_explained():
    """A reader must be told where the placeholder device id comes from."""
    readme = _read(README)
    assert "!secret coffee_device_id" in readme, "no placeholder left to explain"
    assert "/config/devices/device/" in readme, "no way to find the device id"


def test_readme_offers_a_device_independent_trigger():
    """The bus event is the escape hatch when a device id is inconvenient."""
    readme = _read(README)
    bus_event = f"{DOMAIN}_event"
    assert bus_event in readme, "the bus event name is not documented"
    assert "subset match" in readme, "event_data matching semantics not explained"


def test_readme_documents_every_brew_shape():
    """A new shape token must reach the README, not just the payload.

    `shape` is the only description a front-panel brew gets, and the README is
    where a reader learns which values to branch on. Adding a fifth token
    without documenting it would leave an automation silently unmatched.
    """
    from custom_components.melitta_barista.lifecycle import BREW_SHAPE_TOKENS

    readme = _read(README)
    for token in BREW_SHAPE_TOKENS:
        assert f"`{token}`" in readme, f"shape token {token} is undocumented"
