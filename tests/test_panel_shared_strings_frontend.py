"""Contract tests for the panel's 0.94 shared machine-domain strings.

UI Contract §6.3.7 moves four more families of wording from the client
bundles onto the server, served by the existing `melitta_barista/i18n/get`
endpoint: `wizard.*` brew-guide vocabulary, `status.*.<TOKEN>.description`
state descriptions, `sommelier.error.<code>` hints and the
`sommelier.<milk|syrup|topping|liqueur|note>.<token>` labels for the
well-known values of the free-form suggestion fields.

There is no JS test runner in this repo, so — like the other
`*_frontend` tests — these are regex checks over the shipped panel source
plus a `node --check` syntax gate. They pin the three things this round
must not silently lose:

1. **Server first, per family.** Each consumer resolves the served string
   before its own bundle: the wizard's `_t()` consults `serverString(key)`
   ahead of the bundle resolver, the status tab reads
   `status.<kind>.<TOKEN>.description`, the sommelier resolves
   `sommelier.error.<code>` ahead of the legacy `sommelier.err.<code>`,
   and suggestion values go through `freeFormLabel()`.
2. **Fallback retained.** No `www/i18n/locales` key is deleted — the
   bundles stay as tier 2 for offline use and pre-0.94 integrations — and
   every consumer still has a bundle path to fall through to.
3. **The `wizard` domain is actually requested.** `wizard` is the 7th
   member of the served domain set; a client that sends an explicit
   `domains` list must include it. The panel omits the parameter (= all
   domains), which is the durable form — this test fails if a narrowing
   list ever appears without `wizard` in it.

Plus a free-form guard (§9.2.4 / §6.3.7): a served label applies to a
KNOWN token only, so `freeFormLabel()` must fall back to the user's raw
text and must never humanize it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_COMPONENT = _ROOT / "custom_components" / "melitta_barista"
_WWW = _COMPONENT / "www"
_PANEL = _WWW / "melitta-panel.js"
_SERVER_STRINGS = _WWW / "i18n" / "server-strings.js"
_EN_BUNDLE = _WWW / "i18n" / "locales" / "en.js"
_LOCALES_DIR = _WWW / "i18n" / "locales"
_WIZARD = _WWW / "components" / "melitta-brew-wizard.js"
_STATUS = _WWW / "components" / "melitta-status.js"
_SOMMELIER = _WWW / "components" / "melitta-sommelier.js"
_BEANS = _WWW / "components" / "melitta-beans.js"
_ADDITIVES = _WWW / "components" / "melitta-additives.js"
_UI_STRINGS_EN = _COMPONENT / "ui_strings" / "en.json"

_EDITED_FILES = (
    _PANEL,
    _SERVER_STRINGS,
    _WIZARD,
    _STATUS,
    _SOMMELIER,
    _BEANS,
    _ADDITIVES,
)

_SUGGESTION_FAMILIES = ("milk", "syrup", "topping", "liqueur", "note")
_ERROR_CODES = (
    "no_llm_agent",
    "no_llm_agent_selected",
    "llm_agent_missing",
    "timeout",
    "unauthorized",
)

_BUNDLE_KEY_LINE = re.compile(r'^\s*"([^"\\]+)":', re.MULTILINE)
# Literal keys passed to a component's _t()/t() helper.
_T_CALL = re.compile(r'_t\(\s*"([^"]+)"')


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """Source with comments stripped, so structural checks see only code."""
    text = _src(path)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def _bundle_keys(path: Path) -> set[str]:
    return set(_BUNDLE_KEY_LINE.findall(_src(path)))


def _served_keys() -> set[str]:
    return set(json.loads(_src(_UI_STRINGS_EN)))


def _window(text: str, anchor: str, size: int = 700) -> str:
    """The `size` characters of `text` that follow `anchor`."""
    index = text.find(anchor)
    assert index != -1, f"anchor not found in source: {anchor!r}"
    return text[index : index + size]


# ── syntax gate ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _EDITED_FILES, ids=lambda p: p.name)
def test_edited_js_parses(path: Path) -> None:
    """Every touched panel file must be syntactically valid JS."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    result = subprocess.run(
        [node, "--check", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"{path.name} failed node --check:\n{result.stderr}"


# ── 3. the wizard domain is requested ────────────────────────────────────


def test_panel_i18n_request_covers_the_wizard_domain() -> None:
    """i18n/get must not narrow `domains` in a way that drops `wizard`."""
    code = _code(_PANEL)
    call = _window(code, 'type: "melitta_barista/i18n/get"', 300)
    assert "locale," in call or "locale:" in call, (
        "the i18n/get request must carry the locale"
    )
    if "domains" in call:
        assert '"wizard"' in call, (
            "an explicit domains list must include the wizard domain "
            "(§6.3.7: it is the 7th member of the served domain set)"
        )


def test_panel_documents_why_domains_is_omitted() -> None:
    """The omission is deliberate; keep the reason next to the call."""
    src = _src(_PANEL)
    assert "wizard" in src, (
        "melitta-panel.js must record why the i18n/get request omits "
        "`domains` (an omitted list = every domain, wizard included)"
    )


# ── 1a. wizard.* — server first, bundle second ───────────────────────────


def test_wizard_resolver_prefers_the_served_string() -> None:
    """The wizard's _t() consults serverString() before the bundle."""
    code = _code(_WIZARD)
    assert 'from "../i18n/server-strings.js"' in code
    assert "serverString" in code and "formatString" in code
    body = _window(code, "_t(key, params")
    served_at = body.find("serverString(key)")
    bundle_at = body.find("t(key, this.lang")
    assert served_at != -1, "the wizard resolver must consult serverString(key)"
    assert bundle_at != -1, (
        "the wizard resolver must keep the bundle resolver as tier 2"
    )
    assert served_at < bundle_at, (
        "server string must be preferred over the bundle (§6.3.5.1)"
    )


def test_wizard_keys_are_served_and_still_bundled() -> None:
    """Every wizard.* key the panel uses exists on both tiers."""
    used = {k for k in _T_CALL.findall(_src(_WIZARD)) if k.startswith("wizard.")}
    assert len(used) >= 25, f"wizard vocabulary shrank unexpectedly: {len(used)}"
    served = _served_keys()
    missing_server = used - served
    assert not missing_server, (
        f"wizard keys not served by ui_strings/en.json: {sorted(missing_server)}"
    )
    missing_bundle = used - _bundle_keys(_EN_BUNDLE)
    assert not missing_bundle, (
        "the panel bundle must keep every wizard key as the tier-2 fallback: "
        f"{sorted(missing_bundle)}"
    )


def test_wizard_placeholder_semantics_survive_the_server_tier() -> None:
    """The cup step feeds {ml} its unit when the served sentence wins."""
    code = _code(_WIZARD)
    call = _window(code, '"wizard.step.cup"', 300)
    assert "${totalMl} ml" in call, (
        "the served wizard.step.cup carries no unit literal — {ml} must "
        "bring its own unit on the server tier (§6.3.7 placeholders)"
    )
    assert "ml: totalMl" in call, "the bundle tier keeps the bare number"


def test_wizard_bundle_fallback_is_not_deleted_in_any_locale() -> None:
    """All 29 locale bundles keep the wizard keys (tier 2, §6.3.7 (c))."""
    locales = sorted(_LOCALES_DIR.glob("*.js"))
    assert len(locales) == 29, f"expected 29 locale bundles, found {len(locales)}"
    for path in locales:
        keys = _bundle_keys(path)
        assert "wizard.title" in keys and "wizard.step.cup" in keys, (
            f"{path.name} lost its wizard.* fallback entries"
        )


# ── 1b. status descriptions ──────────────────────────────────────────────


def test_status_reads_served_state_descriptions() -> None:
    """The status tab renders status.<kind>.<TOKEN>.description."""
    code = _code(_STATUS)
    assert "serverString" in code, "descriptions come from the server only"
    assert "`status.${kind}.${token}.description`" in code, (
        "descriptions are looked up by exact flat key (§6.3.7: no prefix "
        "scanning, no key nesting)"
    )
    assert '_statusToken("process"' in code
    assert '_statusToken("sub_process"' in code, (
        "sub-process labels/descriptions must reach the UI"
    )


def test_status_descriptions_are_served_for_both_kinds() -> None:
    """en.json actually carries the description keys the panel reads."""
    served = _served_keys()
    process = {
        k for k in served if re.fullmatch(r"status\.process\.[A-Z_0-9]+\.description", k)
    }
    sub = {
        k
        for k in served
        if re.fullmatch(r"status\.sub_process\.[A-Z_0-9]+\.description", k)
    }
    assert len(process) >= 12, f"expected 12 process descriptions, got {len(process)}"
    assert sub, "expected served sub-process descriptions"
    # Every description must sit beside its label in the flat keyspace.
    for key in process | sub:
        assert key[: -len(".description")] in served, (
            f"{key} has no label key beside it"
        )


# ── 1c. sommelier.error.<code> ───────────────────────────────────────────


def test_sommelier_error_hint_prefers_the_served_code() -> None:
    """sommelier.error.<code> wins over the legacy bundle sommelier.err.*."""
    code = _code(_SOMMELIER)
    body = _window(code, "_sommelierErrorHint(code)")
    served_at = body.find("`sommelier.error.${code}`")
    bundle_at = body.find("`sommelier.err.${code}`")
    assert served_at != -1, "the hint must resolve the served error string"
    assert bundle_at != -1, "the bundle hint stays as the tier-2 fallback"
    assert served_at < bundle_at, "server string must be preferred (§6.3.5.1)"
    assert "_sommelierErrorHint(code)" in code.replace(
        "_sommelierErrorHint(code) {", ""
    ), "the generate() error path must call the hint resolver"


def test_sommelier_error_codes_are_served_and_still_bundled() -> None:
    """All five §6.3.7 codes are served; the bundled three stay put."""
    served = _served_keys()
    missing = {c for c in _ERROR_CODES if f"sommelier.error.{c}" not in served}
    assert not missing, f"error codes not served: {sorted(missing)}"
    bundle = _bundle_keys(_EN_BUNDLE)
    for legacy in ("no_llm_agent", "no_llm_agent_selected", "llm_agent_missing"):
        assert f"sommelier.err.{legacy}" in bundle, (
            "the panel bundle must keep its error hints as tier 2"
        )


# ── 1d. suggestion-value labels + free-form guard ────────────────────────


def test_free_form_label_is_server_first_and_never_humanizes() -> None:
    """freeFormLabel: server → bundle → the user's raw text, verbatim."""
    code = _code(_SERVER_STRINGS)
    body = _window(code, "export function freeFormLabel")
    assert "serverString(`sommelier.${family}.${raw}`)" in body, (
        "suggestion labels resolve the served sommelier.<family>.<token> key"
    )
    assert "return raw;" in body, (
        "an unknown value is the user's own text and must render verbatim "
        "(§6.3.7 free-form caveat / §9.2.4)"
    )
    assert "humanizeToken" not in body, (
        "free-form values must never be humanized — that would rewrite what "
        "the user typed"
    )


def test_suggestion_families_are_wired_in_the_consumers() -> None:
    """Each of the five families reaches a consumer through freeFormLabel."""
    sommelier = _code(_SOMMELIER)
    assert "freeFormLabel" in sommelier
    for family in ("syrup", "topping", "liqueur"):
        assert f'_suggestionLabel("{family}"' in sommelier, (
            f"generated-recipe extras must label the {family} value"
        )
    assert '"_allowMilk", "milk"' in sommelier, (
        "the milk add-in chips must resolve the served milk labels"
    )
    beans = _code(_BEANS)
    assert 'freeFormLabel("note"' in beans, (
        "flavour notes must resolve the served sommelier.note.<token> labels"
    )
    assert "_noteLabel(n)" in beans or "_noteLabel(tag)" in beans
    additives = _code(_ADDITIVES)
    assert "freeFormLabel(family, value)" in additives
    assert '_itemLabel("note"' in additives
    assert "_itemLabel(type, r.name)" in additives, (
        "milk/syrup/topping rows must label well-known tokens"
    )


def test_suggestion_labels_keep_raw_values_for_writes() -> None:
    """Labels are display sugar: the raw value stays the stored/sent one."""
    sommelier = _code(_SOMMELIER)
    chip = _window(sommelier, "_renderAddinSection(title, available", 900)
    assert "this._toggle(selectedField, item)" in chip, (
        "the chip must toggle the RAW item, never its label"
    )
    assert "title=${item}" in chip, "the raw value stays visible on hover"
    beans = _code(_BEANS)
    assert "this._removeTagFromBean(tag)" in beans, (
        "note chips must remove the raw note, never its label"
    )


def test_suggestion_families_are_served() -> None:
    """en.json carries the 34 suggestion labels the panel can resolve."""
    served = _served_keys()
    counts = {
        family: len(
            [k for k in served if k.startswith(f"sommelier.{family}.")]
        )
        for family in _SUGGESTION_FAMILIES
    }
    assert all(counts.values()), f"unserved suggestion families: {counts}"
    assert sum(counts.values()) >= 34, f"suggestion label set shrank: {counts}"
