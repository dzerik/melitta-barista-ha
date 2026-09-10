"""Contract tests for the panel's Backup & restore block.

The block lives inside the **Settings subtab of the System tab**
(`www/components/melitta-settings.js`), not in a fifth subtab — that choice
keeps the System shell, its preload list and its subtab labels untouched.

There is no JS test runner in this repo, so — like every other `*_frontend`
test — these are a `node --check` syntax gate plus regex checks over the
shipped source. They pin the handful of things that are easy to lose in a
refactor and expensive to lose in the field:

1. **The download actually happens.** A Blob + object URL + an `<a download>`
   appended to `document.body` (Firefox ignores a click on a detached anchor)
   + `revokeObjectURL` afterwards.
2. **Nothing destructive runs unconfirmed.** Import, snapshot restore and
   snapshot delete each go through `<melitta-confirm>` with
   `destructive: true`, and the confirm is awaited *before* the WebSocket call.
3. **The file input is re-usable.** `e.target.value = ""` after a pick —
   without it, re-picking the same file after a cancelled confirm fires no
   `change` event at all and the button looks dead.
4. **A missing backups directory is not an error.** The snapshot list loads in
   its own try/catch that never assigns `this._error`; a fresh install simply
   has no folder yet, and that must not paint the whole Settings subtab red.
5. **The replaced configuration is not left on screen.** A successful import or
   restore reloads the page, because every already-mounted tab is stale.
6. **Sizes are measured the way the payload travels.** The download is
   written compactly, like every bundle the server serializes, and the import
   guard weighs the re-serialized bundle rather than `file.size` — otherwise
   the page refuses its own exports, whose indentation never reaches the wire.
7. **All 29 locale bundles carry the new keys**, with `{file}` / `{name}` /
   `{count}` placeholders intact (the panel's `t()` substitutes by
   `replaceAll`, so a lost brace silently ships a literal token).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_WWW = _ROOT / "custom_components" / "melitta_barista" / "www"
_SETTINGS = _WWW / "components" / "melitta-settings.js"
_LOCALES_DIR = _WWW / "i18n" / "locales"

_BUNDLE_KEY_LINE = re.compile(r'^\s*"([^"\\]+)":', re.MULTILINE)

_WS_TYPES = (
    "melitta_barista/sommelier/config/export",
    "melitta_barista/sommelier/config/import",
    "melitta_barista/sommelier/config/snapshots/list",
    "melitta_barista/sommelier/config/snapshots/get",
    "melitta_barista/sommelier/config/snapshots/restore",
    "melitta_barista/sommelier/config/snapshots/delete",
)

_BACKUP_KEYS = (
    "backup.title",
    "backup.help",
    "backup.privacy_note",
    "backup.include_history",
    "backup.include_history_help",
    "backup.include_install_specific",
    "backup.include_install_specific_help",
    "backup.export",
    "backup.export_done",
    "backup.export_large",
    "backup.import",
    "backup.import_parse_failed",
    "backup.import_too_large",
    "backup.import_done",
    "backup.snapshots",
    "backup.snapshots_help",
    "backup.snapshots_empty",
    "backup.snapshots_unavailable",
    "backup.snapshot_auto",
    "backup.snapshot_manual",
    "backup.snapshot_download",
    "backup.snapshot_restore",
    "backup.snapshot_delete",
    "backup.restore_done",
    "backup.delete_done",
    "confirm.import.title",
    "confirm.import.message",
    "confirm.import.confirm",
    "confirm.restore.title",
    "confirm.restore.message",
    "confirm.snapshot_delete.title",
    "confirm.snapshot_delete.message",
)

# key -> placeholder that must survive translation.
_PLACEHOLDERS = {
    "backup.import_done": "{count}",
    "confirm.import.message": "{file}",
    "confirm.restore.message": "{name}",
    "confirm.snapshot_delete.message": "{name}",
}


def _src() -> str:
    return _SETTINGS.read_text(encoding="utf-8")


def _code() -> str:
    """Source with comments stripped, so structural checks see only code."""
    text = _src()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def _method(name: str) -> str:
    """The body of one method, from its declaration to the next one.

    Brace-matching would be stricter, but the file's methods are uniformly
    indented by two spaces, so `\\n  <name>(` … up to the next `\\n  ` + name
    is both simpler and stable against the formatting this repo uses.
    """
    code = _code()
    start = code.find(f"async {name}(")
    if start == -1:
        start = code.find(f"\n  {name}(")
    assert start != -1, f"method {name}() not found in melitta-settings.js"
    end = re.search(r"\n  (?:async )?[A-Za-z_]\w*\(", code[start + 10 :])
    return code[start : start + 10 + end.start()] if end else code[start:]


# ── syntax gate ──────────────────────────────────────────────────────────


def test_settings_component_parses() -> None:
    """The edited panel file must be syntactically valid JS."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    result = subprocess.run(
        [node, "--check", str(_SETTINGS)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"melitta-settings.js failed node --check:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "locale", sorted(p.stem for p in _LOCALES_DIR.glob("*.js"))
)
def test_locale_bundle_parses(locale: str) -> None:
    """A locale bundle with a stray quote would take the whole panel down."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    path = _LOCALES_DIR / f"{locale}.js"
    result = subprocess.run(
        [node, "--check", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"{locale}.js failed node --check:\n{result.stderr}"


# ── wiring ───────────────────────────────────────────────────────────────


def test_confirm_component_is_imported_explicitly() -> None:
    """`<melitta-confirm>` must not be borrowed from the shell's preload order."""
    assert 'import "./melitta-confirm.js";' in _src()


@pytest.mark.parametrize("ws_type", _WS_TYPES)
def test_every_backup_ws_command_is_called(ws_type: str) -> None:
    """All six config/* commands are reachable from the panel."""
    assert f'"{ws_type}"' in _code(), f"{ws_type} is never called from the panel"


def test_backup_section_is_not_a_fifth_subtab() -> None:
    """The block stays inside the Settings subtab (no `system.subtabs.backup`)."""
    keys = _BUNDLE_KEY_LINE.findall((_LOCALES_DIR / "en.js").read_text("utf-8"))
    assert "system.subtabs.backup" not in keys


# ── export ───────────────────────────────────────────────────────────────


def test_export_downloads_a_blob_through_a_document_anchor() -> None:
    """Blob -> object URL -> anchor in the document -> click -> revoke."""
    download = _method("_downloadJson")
    assert "new Blob(" in download
    assert "URL.createObjectURL" in download
    assert "a.download = filename" in download
    assert "document.body.appendChild(a)" in download, (
        "Firefox ignores a click on an anchor that is not in the document"
    )
    assert "a.click()" in download
    assert "URL.revokeObjectURL" in download


def test_export_sends_the_include_history_flag() -> None:
    """The export toggle must reach the server, not just the UI."""
    export = _method("_exportConfig")
    assert '"melitta_barista/sommelier/config/export"' in export
    assert "include_history: this._includeHistory" in export


# ── import ───────────────────────────────────────────────────────────────


def test_import_parses_json_before_calling_the_server() -> None:
    """A wrong file is rejected in the browser, before anything is replaced."""
    body = _method("_onImportFile")
    parse_at = body.find("JSON.parse(")
    call_at = body.find("this.hass.callWS(")
    assert parse_at != -1, "the picked file is never parsed in the browser"
    assert call_at != -1
    assert parse_at < call_at, "JSON.parse must precede the import call"
    assert "backup.import_parse_failed" in body


def test_import_is_size_guarded() -> None:
    """Oversized files get a localized hint, not a raw transport error."""
    body = _method("_onImportFile")
    assert "MAX_INLINE_IMPORT_BYTES" in body
    assert "backup.import_too_large" in body
    assert "const MAX_INLINE_IMPORT_BYTES = 3 * 1024 * 1024;" in _code(), (
        "the browser-side cap must mirror sommelier_backup.MAX_INLINE_IMPORT_BYTES"
    )


def test_download_is_written_compactly() -> None:
    """The file on disk must weigh what the export / snapshot rows report.

    `size_bytes` comes from the server's compact serialization, so a
    pretty-printed download is a file the page's own import guard — and the
    `backup.export_large` hint that is meant to predict it — disagree about.
    """
    download = _method("_downloadJson")
    assert "JSON.stringify(bundle)" in download
    assert "null, 2" not in download, (
        "an indented download no longer matches the reported size_bytes"
    )


def test_import_guard_measures_the_payload_not_the_file() -> None:
    """Indentation in the picked file never reaches the WebSocket.

    Guarding on `file.size` rejected bundles this page had just exported: the
    compact frame `hass.callWS` sends is a fraction of the bytes on disk.
    """
    body = _method("_onImportFile")
    assert "file.size > MAX_INLINE_IMPORT_BYTES" not in body, (
        "the transport cap must be applied to the compact payload, not to the file"
    )
    guard_at = body.find("this._payloadBytes(bundle) > MAX_INLINE_IMPORT_BYTES")
    parse_at = body.find("JSON.parse(")
    call_at = body.find("this.hass.callWS(")
    assert guard_at != -1, "the compact payload is never weighed against the cap"
    assert parse_at < guard_at < call_at, (
        "weigh the parsed bundle after parsing and before sending it"
    )
    helper = _method("_payloadBytes")
    assert "new TextEncoder().encode(JSON.stringify(bundle)).length" in helper


def test_picked_file_still_has_a_read_ceiling() -> None:
    """A wildly wrong pick must not be slurped into the tab before parsing."""
    body = _method("_onImportFile")
    assert "file.size > MAX_PICKED_FILE_BYTES" in body
    assert "const MAX_PICKED_FILE_BYTES = " in _code()
    ceiling_at = body.find("MAX_PICKED_FILE_BYTES")
    assert ceiling_at < body.find("await file.text()"), (
        "the read ceiling must be checked before the file is read"
    )


def test_file_input_value_is_reset_after_a_pick() -> None:
    """Without this, re-picking the same file fires no `change` event."""
    body = _method("_onImportFile")
    assert 'e.target.value = "";' in body


def test_import_is_gated_by_a_destructive_confirm() -> None:
    """The replace confirm is awaited before the import call is made."""
    body = _method("_onImportFile")
    confirm_at = body.find("this._confirm(")
    call_at = body.find("this.hass.callWS(")
    assert confirm_at != -1 and confirm_at < call_at, (
        "the destructive confirm must run before the import"
    )
    assert '"confirm.import.title"' in body
    assert '"confirm.import.message"' in body
    assert "destructive: true" in body
    assert "if (!ok) return;" in body


def test_confirm_helper_passes_destructive_through() -> None:
    """The shared helper must not swallow the destructive styling."""
    helper = _method("_confirm")
    assert "melitta-confirm" in helper
    assert "dialog.ask(" in helper
    assert "destructive: Boolean(destructive)" in helper
    assert '"common.cancel"' in helper


def test_import_sends_both_user_choices() -> None:
    """Both checkboxes are part of the import request."""
    body = _method("_onImportFile")
    assert "include_history: this._includeHistory" in body
    assert "include_install_specific: this._includeInstallSpecific" in body


def test_successful_import_reloads_the_page() -> None:
    """Every mounted tab is stale once the configuration has been replaced."""
    body = _method("_onImportFile")
    reload_at = body.find("window.location.reload()")
    assert reload_at != -1, "a successful import must reload the page"
    assert reload_at > body.find("this.hass.callWS("), (
        "the reload must follow the import call, not precede it"
    )


# ── snapshots ────────────────────────────────────────────────────────────


def test_snapshot_list_failure_never_paints_the_settings_banner() -> None:
    """A missing backups directory is the normal fresh-install case."""
    body = _method("_loadSnapshots")
    assert "catch" in body
    assert "this._error" not in body, (
        "a snapshot-list failure must not set the tab-wide error banner"
    )
    assert "this._snapshots = [];" in body
    assert "backup.snapshots_unavailable" in body


def test_snapshot_list_is_loaded_outside_load_all() -> None:
    """`_loadAll()` sets `this._error` on any failure — keep snapshots out of it."""
    load_all = _method("_loadAll")
    assert "snapshots/list" not in load_all
    assert "this._loadSnapshots();" in _method("connectedCallback")


def test_snapshot_restore_is_gated_and_reloads() -> None:
    """Restore is as destructive as an import: same confirm, same reload."""
    body = _method("_restoreSnapshot")
    confirm_at = body.find("this._confirm(")
    call_at = body.find("this.hass.callWS(")
    assert confirm_at != -1 and confirm_at < call_at
    assert '"confirm.restore.title"' in body
    assert "destructive: true" in body
    assert '"melitta_barista/sommelier/config/snapshots/restore"' in body
    assert "name: snap.name" in body
    assert "window.location.reload()" in body


def test_snapshot_delete_is_gated_and_only_refreshes_the_list() -> None:
    """Deleting a file changes nothing else, so no page reload."""
    body = _method("_deleteSnapshot")
    confirm_at = body.find("this._confirm(")
    call_at = body.find("this.hass.callWS(")
    assert confirm_at != -1 and confirm_at < call_at
    assert '"confirm.snapshot_delete.title"' in body
    assert "destructive: true" in body
    assert '"melitta_barista/sommelier/config/snapshots/delete"' in body
    assert "await this._loadSnapshots();" in body
    assert "window.location.reload()" not in body


def test_snapshot_download_reads_the_bundle_server_side() -> None:
    """The browser never re-reads the file; it asks the server for it."""
    body = _method("_downloadSnapshot")
    assert '"melitta_barista/sommelier/config/snapshots/get"' in body
    assert "this._downloadJson(" in body


def test_every_backup_button_honours_the_busy_flag() -> None:
    """Double-clicking a destructive action must not queue a second replace."""
    render = _method("_renderBackup")
    assert render.count("?disabled=${this._backupBusy}") >= 7


# ── locale coverage ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "locale", sorted(p.stem for p in _LOCALES_DIR.glob("*.js"))
)
def test_locale_carries_every_backup_key(locale: str) -> None:
    """All 29 bundles ship the family — `t()` has no per-key server fallback."""
    keys = set(
        _BUNDLE_KEY_LINE.findall((_LOCALES_DIR / f"{locale}.js").read_text("utf-8"))
    )
    missing = [k for k in _BACKUP_KEYS if k not in keys]
    assert not missing, f"{locale}.js is missing {missing}"


@pytest.mark.parametrize(
    "locale", sorted(p.stem for p in _LOCALES_DIR.glob("*.js"))
)
def test_locale_keeps_the_substitution_placeholders(locale: str) -> None:
    """`t()` substitutes by replaceAll — a lost brace ships a literal token."""
    text = (_LOCALES_DIR / f"{locale}.js").read_text("utf-8")
    for key, token in _PLACEHOLDERS.items():
        line = re.search(rf'^\s*"{re.escape(key)}": "(.*)",$', text, re.MULTILINE)
        assert line is not None, f"{locale}.js has no single-line value for {key}"
        assert token in line.group(1), f"{locale}.js lost {token} in {key}"


@pytest.mark.parametrize(
    "locale",
    sorted(p.stem for p in _LOCALES_DIR.glob("*.js") if p.stem != "en"),
)
def test_translations_are_not_english_copies(locale: str) -> None:
    """A locale that merely echoed English would defeat the whole family.

    Compared on the long help strings only: short button labels legitimately
    coincide with English in several languages (Download, Import, Automatik…).
    """
    en = (_LOCALES_DIR / "en.js").read_text("utf-8")
    other = (_LOCALES_DIR / f"{locale}.js").read_text("utf-8")
    for key in ("backup.help", "backup.privacy_note", "backup.snapshots_help"):
        pattern = rf'^\s*"{re.escape(key)}": "(.*)",$'
        en_value = re.search(pattern, en, re.MULTILINE).group(1)
        value = re.search(pattern, other, re.MULTILINE).group(1)
        assert value != en_value, f"{locale}.js: {key} is an English copy"
