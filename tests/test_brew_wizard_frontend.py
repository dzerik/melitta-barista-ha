"""Contract tests for the step-machine brew wizard frontend.

There is no JS test runner in this repo, so — like test_i18n_parity.py —
these tests are regex-based checks over the shipped source. They pin the
frontend half of the 0.89 wizard contract:

1. The wizard drives per-phase brewing through the
   ``melitta_barista/sommelier/brew_phase`` WS command (one call per
   machine phase, 0-based ``phase_index``).
2. Completion/prompt detection reads the nested status fields the
   backend added for the wizard: ``status.is_brewing`` and
   ``status.awaiting_confirmation`` (the old top-level ``is_brewing``
   read was dead code — the field never existed at top level).
3. Inter-phase manual actions (``user_action_before``) are rendered.
4. Wizard position survives dialog close/reopen (localStorage).
5. Machine prompts can be acknowledged via the ``confirm_prompt``
   service.
6. No fixed ``min-width: 360px`` — the dialog must not overflow small
   phones (audit: min-width beats max-width in CSS).
7. Every i18n key the wizard references exists in en.js, and every
   ``wizard.*`` key in en.js is actually referenced (no stale keys).
   Cross-locale parity itself is covered by test_i18n_parity.py.
"""
from __future__ import annotations

import re
from pathlib import Path

_WWW = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "www"
)
_WIZARD = _WWW / "components" / "melitta-brew-wizard.js"
_EN = _WWW / "i18n" / "locales" / "en.js"
_RU = _WWW / "i18n" / "locales" / "ru.js"

_KEY_LINE = re.compile(r'^\s*"([^"\\]+)":', re.MULTILINE)
# Literal keys passed to the component's _t()/t() helpers. Dynamic keys
# (template-string lookups like `sommelier.cup.${x}`) are exempt by
# construction — the wizard must not build dynamic "wizard.*" keys.
_T_CALL = re.compile(r'_t\(\s*"([^"]+)"')


def _wizard_src() -> str:
    return _WIZARD.read_text(encoding="utf-8")


def _en_keys() -> set[str]:
    return set(_KEY_LINE.findall(_EN.read_text(encoding="utf-8")))


def _ru_keys() -> set[str]:
    return set(_KEY_LINE.findall(_RU.read_text(encoding="utf-8")))


def test_wizard_uses_per_phase_brew_command():
    """Machine steps must go through the per-phase WS command."""
    src = _wizard_src()
    assert "melitta_barista/sommelier/brew_phase" in src, (
        "wizard must brew via the per-phase WS command"
    )
    assert "phase_index" in src, "brew_phase call must carry phase_index"


def test_wizard_reads_nested_wizard_status_fields():
    """Completion polling must read the nested status dict fields."""
    src = _wizard_src()
    assert "is_brewing" in src
    assert "awaiting_confirmation" in src
    # The old bug was reading a top-level `status.is_brewing` off the WS
    # reply root. The field lives in the NESTED status dict, so the
    # wizard must dereference one level down before touching is_brewing.
    assert re.search(r"payload\??\.status|\.status\s*\|\||\bst\b", src), (
        "wizard must unwrap the nested status dict from the WS payload"
    )


def test_wizard_renders_inter_phase_manual_actions():
    """machine_phases[*].user_action_before must reach the UI."""
    assert "user_action_before" in _wizard_src()


def test_wizard_persists_position_for_reentry():
    """Closing mid-brew must not lose the user's position."""
    assert "localStorage" in _wizard_src()


def test_wizard_can_acknowledge_machine_prompts():
    """Manipulation prompts surface a confirm action (confirm_prompt)."""
    src = _wizard_src()
    assert "confirm_prompt" in src
    assert "callService" in src, (
        "confirm_prompt is an HA service (entity_id-addressed), "
        "not a WS command"
    )


def test_wizard_has_no_fixed_min_width():
    """A hard min-width:360px overflows 360px phones (92vw max-width)."""
    assert not re.search(r"min-width:\s*360px\s*;", _wizard_src()), (
        "use min(360px, calc(100vw - Npx)) instead of a fixed min-width"
    )


def test_wizard_i18n_keys_exist_in_en():
    """Every literal key the wizard translates must exist in en.js."""
    used = set(_T_CALL.findall(_wizard_src()))
    assert used, "wizard must localize its strings through _t()"
    missing = {k for k in used if not k.startswith("sommelier.cup.")} - _en_keys()
    assert not missing, f"keys used by the wizard but absent in en.js: {sorted(missing)}"


def test_en_has_no_stale_wizard_keys():
    """Every wizard.* key in en.js must be referenced by the wizard."""
    used = set(_T_CALL.findall(_wizard_src()))
    stale = {k for k in _en_keys() if k.startswith("wizard.")} - used
    assert not stale, f"stale wizard.* keys in en.js: {sorted(stale)}"


def test_en_carries_step_machine_keys():
    """The new step-machine key set must land in en.js."""
    required = {
        "wizard.step_of",
        "wizard.step.cup",
        "wizard.step.done",
        "wizard.machine.start",
        "wizard.machine.retry",
        "wizard.machine.prompt",
        "wizard.machine.confirm",
        "wizard.close.title",
        "wizard.close.stay",
        "wizard.close.leave",
        "wizard.resumed",
        "wizard.restart",
        "wizard.finish.title",
        "wizard.finish.button",
    }
    missing = required - _en_keys()
    assert not missing, f"missing step-machine keys in en.js: {sorted(missing)}"


def test_wizard_i18n_keys_exist_in_ru():
    """Russian must carry every wizard key too (both shipped locales)."""
    used = set(_T_CALL.findall(_wizard_src()))
    missing = {k for k in used if not k.startswith("sommelier.cup.")} - _ru_keys()
    assert not missing, f"keys used by the wizard but absent in ru.js: {sorted(missing)}"
