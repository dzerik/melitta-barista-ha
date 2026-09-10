/**
 * Settings tab.
 *
 * - LLM model picker: dropdown of HA conversation agents; the selected
 *   agent_id is persisted via `melitta_barista/sommelier/settings/set`
 *   (key=`llm_agent_id`) and reused by both Sommelier generation and Bean
 *   autofill. Choosing "default" stores an empty string, which makes the
 *   backend fall back to HA's default agent.
 *
 * - Prompt templates editor: every LLM-bound request type has a default
 *   prompt bundled in the integration; users can override it. Saving an
 *   empty / identical template falls back to the default. The list of slots
 *   comes from `melitta_barista/prompts/list` so this UI auto-grows when new
 *   slots are introduced.
 *
 * - Backup & restore: export the whole Sommelier configuration as one JSON
 *   bundle, import one back (a full REPLACE, behind a destructive confirm),
 *   and manage the pre-import snapshots the server writes before every
 *   replace. It lives here rather than in a 5th subtab so the System tab's
 *   shell, its preload list and its subtab labels stay untouched.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n/index.js";
import "./melitta-confirm.js";

/**
 * Largest bundle the import command accepts inline over the WebSocket,
 * mirroring `sommelier_backup.MAX_INLINE_IMPORT_BYTES` (3 MiB).
 *
 * Checked in the browser so an oversized file yields a localized hint that
 * names the way out (restore it from the snapshot list, which the server
 * reads off disk) instead of a raw transport error.
 *
 * It caps the *compact* JSON that `hass.callWS` puts on the wire — the same
 * number the export and snapshot rows report — not the bytes a file happens
 * to occupy on disk, so whitespace in a picked file never decides it.
 */
const MAX_INLINE_IMPORT_BYTES = 3 * 1024 * 1024;

/**
 * Ceiling on the raw file the picker will read into memory at all.
 *
 * The transport cap above can only be applied after parsing, so this loose
 * second ceiling exists purely to keep a wildly wrong pick (a video, a
 * database) from being slurped into the tab. Any real bundle whose compact
 * form fits in 3 MiB stays far below it even when pretty-printed by hand.
 */
const MAX_PICKED_FILE_BYTES = 32 * 1024 * 1024;

class MelittaSettings extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _agents: { type: Array },
      _timeoutS: { type: String },
      _compactPrompt: { type: Boolean },
      _selectedAgent: { type: String },
      _prompts: { type: Array },
      _drafts: { type: Object },
      _info: { type: String },
      _error: { type: String },
      _previewSlot: { type: String },
      _previewText: { type: String },
      _previewLoading: { type: Boolean },
      _backupBusy: { type: Boolean },
      _includeHistory: { type: Boolean },
      _includeInstallSpecific: { type: Boolean },
      _snapshots: { type: Array },
      _snapshotsError: { type: String },
      _exportSize: { type: Number },
    };
  }

  constructor() {
    super();
    this._agents = [];
    this._timeoutS = "";
    this._compactPrompt = false;
    this._selectedAgent = "";
    this._prompts = [];
    this._drafts = {};
    this._info = "";
    this._error = "";
    this._previewSlot = "";
    this._previewText = "";
    this._previewLoading = false;
    this._backupBusy = false;
    this._includeHistory = false;
    // Default ON, matching the import command's own default: a bundle moved
    // between installations normally carries the agent it was built against.
    this._includeInstallSpecific = true;
    this._snapshots = [];
    this._snapshotsError = "";
    this._exportSize = 0;
  }

  async _openPreview(slot) {
    this._previewSlot = slot;
    this._previewText = "";
    this._previewLoading = true;
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/prompts/preview",
        slot,
      });
      this._previewText = result.prompt || "";
    } catch (e) {
      this._previewText = `Error: ${e.message || e}`;
    } finally {
      this._previewLoading = false;
    }
  }

  _closePreview() {
    this._previewSlot = "";
    this._previewText = "";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadAll();
    // Deliberately NOT part of _loadAll(): see _loadSnapshots().
    this._loadSnapshots();
  }

  async _loadAll() {
    try {
      const [a, s, p] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/llm/agents" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/settings/get" }),
        this.hass.callWS({ type: "melitta_barista/prompts/list" }),
      ]);
      this._agents = a.agents || [];
      this._selectedAgent = (s.settings || {}).llm_agent_id || "";
      this._timeoutS = (s.settings || {}).llm_timeout_s || "60";
      this._compactPrompt = (s.settings || {}).compact_prompt === "true";
      this._prompts = p.prompts || [];
      const drafts = {};
      for (const item of this._prompts) drafts[item.slot] = item.template;
      this._drafts = drafts;
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _saveTimeout(value) {
    this._info = "";
    const n = parseInt(value, 10);
    if (!Number.isFinite(n) || n < 10 || n > 600) {
      this._error = this._t("settings.llm_timeout_invalid");
      return;
    }
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/settings/set",
        key: "llm_timeout_s",
        value: String(n),
      });
      this._error = "";
      this._info = this._t("settings.saved");
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _saveCompactPrompt(enabled) {
    this._compactPrompt = enabled;
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/settings/set",
        key: "compact_prompt",
        value: enabled ? "true" : "false",
      });
      this._error = "";
      this._info = this._t("settings.saved");
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _saveAgent(agentId) {
    this._selectedAgent = agentId;
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/settings/set",
        key: "llm_agent_id",
        value: agentId,
      });
      this._info = this._t("settings.saved");
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _savePrompt(slot) {
    const template = this._drafts[slot] ?? "";
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/prompts/save",
        slot, template,
      });
      this._info = this._t("settings.saved");
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _resetPrompt(slot) {
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/prompts/reset",
        slot,
      });
      this._info = this._t("settings.reset_done");
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _onPromptInput(slot, value) {
    this._drafts = { ...this._drafts, [slot]: value };
  }

  // ── Backup & restore ───────────────────────────────────────────────

  /**
   * Refresh the snapshot list in its OWN try/catch.
   *
   * It never assigns `this._error`: a fresh install simply has no backups
   * directory yet, and that entirely normal case must not paint the whole
   * Settings subtab with the failure banner. Any failure degrades to an
   * empty list plus a local hint rendered next to the list itself.
   */
  async _loadSnapshots() {
    try {
      const res = await this.hass.callWS({
        type: "melitta_barista/sommelier/config/snapshots/list",
      });
      this._snapshots = res.snapshots || [];
      this._snapshotsError = "";
    } catch (e) {
      this._snapshots = [];
      this._snapshotsError = this._t("backup.snapshots_unavailable");
    }
  }

  /** Byte count as a short human-readable size for the export / snapshot rows. */
  _fmtSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** A snapshot's ISO `created_at` in the browser's own locale, or verbatim. */
  _fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
  }

  /**
   * Hand a bundle to the browser as a downloaded JSON file.
   *
   * The panel is registered with `embed_iframe: False`, so this runs in the
   * main HA document where Blob URLs and `<a download>` behave; the anchor is
   * appended to the document because Firefox ignores a click on a detached one.
   *
   * Written compactly, exactly as the server serializes bundles: the file on
   * disk then weighs what the export and snapshot rows say it weighs, and a
   * bundle that was small enough to export is small enough to import back.
   */
  _downloadJson(bundle, filename) {
    const blob = new Blob([JSON.stringify(bundle)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  /**
   * Open <melitta-confirm> and await the user's decision.
   *
   * Lazy-creates the dialog inside this component's shadow root — the same
   * pattern as melitta-beans.js — so nothing is rendered until something asks.
   */
  async _confirm({ title, message, confirmLabel, destructive }) {
    let dialog = this.renderRoot.querySelector("melitta-confirm");
    if (!dialog) {
      dialog = document.createElement("melitta-confirm");
      this.renderRoot.appendChild(dialog);
    }
    return dialog.ask({
      title,
      message,
      confirmLabel,
      cancelLabel: this._t("common.cancel"),
      destructive: Boolean(destructive),
    });
  }

  /** Total rows written by an import, summed over the per-table counts. */
  _totalImported(result) {
    const counts = (result || {}).imported || {};
    return Object.values(counts).reduce((sum, n) => sum + (Number(n) || 0), 0);
  }

  /**
   * Build the bundle server-side and hand it to the browser as a download.
   *
   * The reported size is kept so an export too big to come back in through
   * this page can say so instead of failing on the next import attempt.
   */
  async _exportConfig() {
    this._info = "";
    this._error = "";
    this._exportSize = 0;
    this._backupBusy = true;
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/sommelier/config/export",
        include_history: this._includeHistory,
      });
      const stamp = new Date().toISOString().slice(0, 10);
      this._downloadJson(result.export, `melitta-sommelier-${stamp}.json`);
      this._exportSize = result.size_bytes || 0;
      this._info = this._t("backup.export_done");
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._backupBusy = false;
    }
  }

  /** Byte length of a bundle as `hass.callWS` will actually serialize it. */
  _payloadBytes(bundle) {
    return new TextEncoder().encode(JSON.stringify(bundle)).length;
  }

  /**
   * Handle a picked file: parse it here, confirm, then replace and reload.
   *
   * The input value is cleared straight away — without that, re-picking the
   * same file after a cancelled confirm fires no `change` event at all. The
   * JSON is parsed in the browser so a wrong file is rejected before anything
   * server-side is touched, and the page is reloaded on success because every
   * already-mounted tab is stale once the configuration has been replaced.
   *
   * The size guard is applied to the re-serialized compact payload rather than
   * to `file.size`: indentation in the file never reaches the WebSocket, and
   * measuring the file instead used to refuse bundles this page had just
   * exported at a size the transport carries fine.
   */
  async _onImportFile(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    this._info = "";
    this._error = "";
    if (file.size > MAX_PICKED_FILE_BYTES) {
      this._error = this._t("backup.import_too_large");
      return;
    }
    let bundle;
    try {
      bundle = JSON.parse(await file.text());
    } catch (err) {
      this._error = this._t("backup.import_parse_failed");
      return;
    }
    if (this._payloadBytes(bundle) > MAX_INLINE_IMPORT_BYTES) {
      this._error = this._t("backup.import_too_large");
      return;
    }
    const ok = await this._confirm({
      title: this._t("confirm.import.title"),
      message: this._t("confirm.import.message", { file: file.name }),
      confirmLabel: this._t("confirm.import.confirm"),
      destructive: true,
    });
    if (!ok) return;
    this._backupBusy = true;
    try {
      const res = await this.hass.callWS({
        type: "melitta_barista/sommelier/config/import",
        export: bundle,
        include_history: this._includeHistory,
        include_install_specific: this._includeInstallSpecific,
      });
      this._info = this._t("backup.import_done", {
        count: this._totalImported(res),
      });
      window.location.reload();
    } catch (err) {
      this._error = err.message || String(err);
    } finally {
      this._backupBusy = false;
    }
  }

  /** Download one snapshot; the server reads and parses it, never the browser. */
  async _downloadSnapshot(snap) {
    this._info = "";
    this._error = "";
    this._backupBusy = true;
    try {
      const res = await this.hass.callWS({
        type: "melitta_barista/sommelier/config/snapshots/get",
        name: snap.name,
      });
      this._downloadJson(res.export, snap.name);
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._backupBusy = false;
    }
  }

  /**
   * Restore a snapshot server-side.
   *
   * Just as destructive as an import — it is a full replace as well — so it
   * goes through the same confirm and the same reload. The bundle is never
   * uploaded: only its name travels, which is also how a snapshot too large
   * to travel inbound is still restorable.
   */
  async _restoreSnapshot(snap) {
    this._info = "";
    this._error = "";
    const ok = await this._confirm({
      title: this._t("confirm.restore.title"),
      message: this._t("confirm.restore.message", { name: snap.name }),
      confirmLabel: this._t("backup.snapshot_restore"),
      destructive: true,
    });
    if (!ok) return;
    this._backupBusy = true;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/config/snapshots/restore",
        name: snap.name,
      });
      this._info = this._t("backup.restore_done");
      window.location.reload();
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._backupBusy = false;
    }
  }

  /**
   * Delete one snapshot file.
   *
   * Retention prunes only the automatic pre-import snapshots, so this is the
   * only way to get rid of a kept one. Nothing but the file changes, so the
   * list is refreshed in place — no page reload.
   */
  async _deleteSnapshot(snap) {
    this._info = "";
    this._error = "";
    const ok = await this._confirm({
      title: this._t("confirm.snapshot_delete.title"),
      message: this._t("confirm.snapshot_delete.message", { name: snap.name }),
      confirmLabel: this._t("backup.snapshot_delete"),
      destructive: true,
    });
    if (!ok) return;
    this._backupBusy = true;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/config/snapshots/delete",
        name: snap.name,
      });
      this._info = this._t("backup.delete_done");
      await this._loadSnapshots();
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._backupBusy = false;
    }
  }

  /** The Backup & restore block, rendered at the end of the Settings card. */
  _renderBackup() {
    return html`
      <h3>${this._t("backup.title")}</h3>
      <p class="help">${this._t("backup.help")}</p>
      <p class="help privacy">${this._t("backup.privacy_note")}</p>

      <label class="toggle">
        <input type="checkbox"
          ?disabled=${this._backupBusy}
          .checked=${this._includeHistory}
          @change=${(e) => { this._includeHistory = e.target.checked; }} />
        ${this._t("backup.include_history")}
      </label>
      <p class="help">${this._t("backup.include_history_help")}</p>

      <label class="toggle">
        <input type="checkbox"
          ?disabled=${this._backupBusy}
          .checked=${this._includeInstallSpecific}
          @change=${(e) => { this._includeInstallSpecific = e.target.checked; }} />
        ${this._t("backup.include_install_specific")}
      </label>
      <p class="help">${this._t("backup.include_install_specific_help")}</p>

      ${this._exportSize > MAX_INLINE_IMPORT_BYTES
        ? html`<div class="hint">${this._t("backup.export_large")}</div>`
        : ""}

      <div class="form-actions">
        <input type="file" class="import-file" accept="application/json,.json" hidden
          @change=${(e) => this._onImportFile(e)} />
        <button class="ghost" ?disabled=${this._backupBusy}
          @click=${() => this.renderRoot.querySelector("input.import-file").click()}>
          ${this._t("backup.import")}
        </button>
        <button class="primary" ?disabled=${this._backupBusy}
          @click=${() => this._exportConfig()}>
          ${this._t("backup.export")}
        </button>
      </div>

      <h3>${this._t("backup.snapshots")}</h3>
      <p class="help">${this._t("backup.snapshots_help")}</p>
      ${this._snapshotsError
        ? html`<div class="hint">${this._snapshotsError}</div>`
        : this._snapshots.length === 0
          ? html`<div class="hint">${this._t("backup.snapshots_empty")}</div>`
          : this._snapshots.map((s) => html`
            <div class="snapshot">
              <div class="snap-meta">
                <code>${s.name}</code>
                <span class="badge">
                  ${this._t(s.source === "auto"
                    ? "backup.snapshot_auto"
                    : "backup.snapshot_manual")}
                </span>
                <span class="snap-sub">
                  ${this._fmtDate(s.created_at)} · ${this._fmtSize(s.size_bytes)}
                </span>
              </div>
              <div class="snap-actions">
                <button class="ghost" ?disabled=${this._backupBusy}
                  @click=${() => this._downloadSnapshot(s)}>
                  ${this._t("backup.snapshot_download")}
                </button>
                <button class="ghost" ?disabled=${this._backupBusy}
                  @click=${() => this._restoreSnapshot(s)}>
                  ${this._t("backup.snapshot_restore")}
                </button>
                <button class="ghost danger" ?disabled=${this._backupBusy}
                  @click=${() => this._deleteSnapshot(s)}>
                  ${this._t("backup.snapshot_delete")}
                </button>
              </div>
            </div>
          `)}
    `;
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("settings.title")}</h2>
        ${this._error ? html`<div class="error">${this._error}</div>` : ""}
        ${this._info ? html`<div class="info">${this._info}</div>` : ""}

        <h3>${this._t("settings.llm_agent")}</h3>
        <p class="help">${this._t("settings.llm_help")}</p>
        <select class="agent"
          .value=${this._selectedAgent}
          @change=${(e) => this._saveAgent(e.target.value)}>
          <option value="" ?selected=${!this._selectedAgent}>— HA default —</option>
          ${this._agents.map((a) => html`
            <option value=${a.id} ?selected=${a.id === this._selectedAgent}>
              ${a.name || a.id}
            </option>
          `)}
        </select>

        <h3>${this._t("settings.llm_timeout")}</h3>
        <p class="help">${this._t("settings.llm_timeout_help")}</p>
        <input type="number" min="10" max="600" step="10"
          .value=${this._timeoutS || "60"}
          @change=${(e) => { this._timeoutS = e.target.value; this._saveTimeout(e.target.value); }} />

        <h3>${this._t("settings.compact_prompt")}</h3>
        <p class="help">${this._t("settings.compact_prompt_help")}</p>
        <label class="toggle">
          <input type="checkbox"
            .checked=${this._compactPrompt}
            @change=${(e) => this._saveCompactPrompt(e.target.checked)} />
          ${this._t("settings.compact_prompt_label")}
        </label>

        <h3>${this._t("settings.prompts")}</h3>

        <details class="help" open>
          <summary>${this._t("settings.help_title")}</summary>
          <div class="help-body">
            <p><strong>${this._t("settings.help_syntax")}.</strong>
              ${this._t("settings.help_syntax_text")}</p>
            <p>${this._t("settings.help_schema")}</p>
            <p>${this._t("settings.help_smartchain")}</p>
          </div>
        </details>

        ${this._previewSlot ? html`
          <melitta-modal .open=${true}
            .title=${`${this._t("settings.preview_title")} — ${this._previewSlot}`}
            @close=${() => this._closePreview()}>
            ${this._previewLoading
              ? html`<div class="hint">${this._t("settings.preview_loading")}</div>`
              : html`<pre class="preview">${this._previewText}</pre>`}
          </melitta-modal>
        ` : ""}

        ${this._prompts.length === 0
          ? html`<div class="hint">${this._t("common.empty")}</div>`
          : this._prompts.map((p) => html`
            <details class="prompt" ?open=${!p.is_default}>
              <summary>
                <code>${p.slot}</code>
                ${p.is_default ? html`<span class="badge">${this._t("settings.prompt_default")}</span>` : ""}
              </summary>

              <div class="placeholders">
                <span class="ph-label">${this._t("settings.help_placeholders")}:</span>
                ${(p.placeholders || []).length === 0
                  ? html`<span class="ph-empty">${this._t("settings.help_no_placeholders")}</span>`
                  : (p.placeholders || []).map((ph) => html`
                    <code class="ph">{${ph.name}}</code>
                    <span class="ph-desc">— ${ph.desc}</span>
                  `)}
              </div>

              <textarea
                rows="10"
                .value=${this._drafts[p.slot] ?? ""}
                @input=${(e) => this._onPromptInput(p.slot, e.target.value)}
              ></textarea>
              <div class="form-actions">
                <button class="ghost" @click=${() => this._openPreview(p.slot)}>
                  ${this._t("settings.preview")}
                </button>
                <button class="ghost" @click=${() => this._resetPrompt(p.slot)}>
                  ${this._t("settings.prompt_reset")}
                </button>
                <button class="primary" @click=${() => this._savePrompt(p.slot)}>
                  ${this._t("settings.prompt_save")}
                </button>
              </div>
              ${p.schema ? html`
                <details class="schema">
                  <summary>JSON Schema (auto-appended, read-only)</summary>
                  <pre>${JSON.stringify(p.schema, null, 2)}</pre>
                </details>
              ` : ""}
            </details>
          `)}

        ${this._renderBackup()}
      </section>
    `;
  }

  static get styles() {
    return css`
      .card {
        background: var(--card-background-color);
        border-radius: 8px;
        padding: 16px 20px;
        box-shadow: var(--ha-card-box-shadow);
      }
      h2 { margin: 0 0 12px; font-size: 18px; }
      h3 {
        margin: 24px 0 4px;
        font-size: 14px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .help {
        margin: 0 0 8px;
        color: var(--secondary-text-color);
        font-size: 13px;
      }
      select.agent {
        width: 100%;
        max-width: 480px;
        padding: 8px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 14px;
      }
      label.toggle {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-size: 14px;
        color: var(--primary-text-color);
        cursor: pointer;
      }
      label.toggle input[type="checkbox"] {
        width: 16px;
        height: 16px;
        accent-color: var(--primary-color);
        cursor: pointer;
      }
      details.prompt {
        margin-top: 8px;
        background: var(--secondary-background-color);
        border-radius: 4px;
        padding: 8px 12px;
      }
      details.prompt summary {
        cursor: pointer;
        font-size: 13px;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      details.prompt summary code {
        font-family: var(--code-font-family, monospace);
      }
      .badge {
        font-size: 11px;
        padding: 1px 6px;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border-radius: 8px;
      }
      details.help {
        margin: 8px 0 16px;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 13px;
      }
      details.help summary { cursor: pointer; font-weight: 500; }
      details.help .help-body { padding-top: 6px; }
      details.help p { margin: 6px 0; line-height: 1.45; }
      details.help code, details.help strong { font-weight: 500; }
      pre.preview {
        background: var(--primary-background-color);
        padding: 12px;
        border-radius: 4px;
        max-height: 70vh;
        overflow: auto;
        white-space: pre-wrap;
        word-break: break-word;
        font-family: var(--code-font-family, monospace);
        font-size: 12px;
        line-height: 1.45;
        margin: 0;
      }

      .placeholders {
        font-size: 12px;
        color: var(--secondary-text-color);
        background: var(--primary-background-color);
        border-radius: 4px;
        padding: 6px 10px;
        margin: 8px 0;
        display: flex;
        flex-wrap: wrap;
        gap: 4px 6px;
        align-items: baseline;
      }
      .ph-label {
        font-weight: 500;
        margin-right: 4px;
      }
      .ph-empty { font-style: italic; }
      .ph {
        background: var(--secondary-background-color);
        padding: 1px 6px;
        border-radius: 3px;
        font-family: var(--code-font-family, monospace);
        color: var(--primary-text-color);
      }
      .ph-desc { margin-right: 6px; }

      details.schema {
        margin-top: 8px;
        background: var(--primary-background-color);
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 11px;
      }
      details.schema summary {
        cursor: pointer;
        color: var(--secondary-text-color);
        font-size: 11px;
      }
      details.schema pre {
        margin: 6px 0 0;
        white-space: pre-wrap;
        word-break: break-word;
        font-family: var(--code-font-family, monospace);
        color: var(--secondary-text-color);
      }
      details.prompt textarea {
        width: 100%;
        margin-top: 8px;
        padding: 8px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--code-font-family, monospace);
        font-size: 12px;
        line-height: 1.4;
        resize: vertical;
      }
      .form-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 8px;
      }
      button.primary {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.ghost {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      .info {
        margin: 8px 0;
        padding: 8px 12px;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border-radius: 4px;
        font-size: 13px;
      }
      .error {
        margin: 8px 0;
        padding: 8px 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
        font-size: 13px;
      }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      button.danger {
        color: var(--error-color);
        border-color: var(--error-color);
      }
      p.help.privacy { font-style: italic; }
      .snapshot {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-top: 8px;
        padding: 8px 12px;
        background: var(--secondary-background-color);
        border-radius: 4px;
      }
      .snap-meta {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 4px 8px;
        min-width: 0;
      }
      .snap-meta code {
        font-family: var(--code-font-family, monospace);
        font-size: 12px;
        word-break: break-all;
      }
      .snap-sub {
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .snap-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      .snap-actions button {
        font-size: 12px;
        padding: 4px 10px;
      }
    `;
  }
}

if (!customElements.get('melitta-settings')) customElements.define('melitta-settings', MelittaSettings);
