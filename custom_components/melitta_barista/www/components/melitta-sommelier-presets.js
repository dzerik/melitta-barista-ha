/**
 * <melitta-sommelier-presets> — modal listing user-defined Sommelier presets.
 *
 * Public props:
 *   - hass: HomeAssistant
 *   - lang: string
 *   - open: boolean
 *
 * Dispatches:
 *   - @close: user closes the modal (parent owns the `open` flag).
 *
 * Loads the preset list on every open via
 * `melitta_barista/sommelier/presets/list`. Each row supports inline
 * rename + edit-description (one combined form, mirroring favorites)
 * and delete via <melitta-confirm>. Creation lives in the Sommelier
 * header — this modal manages existing presets only.
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";
import { t } from "../i18n/index.js";
import "./melitta-modal.js";
import "./melitta-confirm.js";

class MelittaSommelierPresets extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      lang: { type: String },
      open: { type: Boolean, reflect: true },
      _presets: { state: true },
      _editing: { state: true },
      _editName: { state: true },
      _editDescription: { state: true },
      _error: { state: true },
      _saving: { state: true },
    };
  }

  constructor() {
    super();
    this.open = false;
    this._presets = [];
    this._editing = null;
    this._editName = "";
    this._editDescription = "";
    this._error = "";
    this._saving = false;
  }

  _t(key, params) { return t(key, this.lang || "en", params); }

  updated(changed) {
    if (changed.has("open") && this.open) {
      this._editing = null;
      this._loadPresets();
    }
  }

  async _loadPresets() {
    if (!this.hass) return;
    this._error = "";
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/sommelier/presets/list",
      });
      this._presets = result.presets || [];
    } catch (e) {
      this._error = `${this._t("presets.load_failed")}: ${e.message || e}`;
    }
  }

  _close() {
    this.dispatchEvent(new CustomEvent("close", { bubbles: true, composed: true }));
  }

  _startEdit(preset) {
    // System presets are read-only — the UI never renders this button for
    // them, but guard defensively so a programmatic call can't open the form.
    if (preset?.is_system) return;
    this._editing = preset.id;
    this._editName = preset.name || "";
    this._editDescription = preset.description || "";
    this._error = "";
  }

  _cancelEdit() {
    this._editing = null;
  }

  async _saveEdit(preset) {
    const name = (this._editName || "").trim();
    if (!name) return;
    this._saving = true;
    this._error = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/presets/update",
        preset_id: preset.id,
        name,
        description: this._editDescription || "",
      });
      this._editing = null;
      await this._loadPresets();
    } catch (e) {
      this._error = `${this._t("presets.save_failed")}: ${e.message || e}`;
    } finally {
      this._saving = false;
    }
  }

  async _onDelete(preset) {
    let dialog = this.renderRoot.querySelector("melitta-confirm");
    if (!dialog) {
      dialog = document.createElement("melitta-confirm");
      this.renderRoot.appendChild(dialog);
    }
    const ok = await dialog.ask({
      title: this._t("presets.delete"),
      message: this._t("presets.delete_confirm"),
      confirmLabel: this._t("common.delete"),
      cancelLabel: this._t("common.cancel"),
      destructive: true,
    });
    if (!ok) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/presets/delete",
        preset_id: preset.id,
      });
      this._presets = this._presets.filter((p) => p.id !== preset.id);
    } catch (e) {
      this._error = `${this._t("presets.delete_failed")}: ${e.message || e}`;
    }
  }

  _renderPreset(preset) {
    if (this._editing === preset.id) {
      return html`
        <div class="card editing">
          <input
            class="edit-name"
            .value=${this._editName}
            placeholder=${this._t("presets.name")}
            @input=${(e) => { this._editName = e.target.value; }}>
          <textarea
            class="edit-desc"
            .value=${this._editDescription}
            placeholder=${this._t("presets.description")}
            @input=${(e) => { this._editDescription = e.target.value; }}></textarea>
          <div class="actions">
            <button class="ghost" ?disabled=${this._saving}
                    @click=${() => this._cancelEdit()}>${this._t("common.cancel")}</button>
            <button class="primary" ?disabled=${this._saving}
                    @click=${() => this._saveEdit(preset)}>
              ${this._saving ? this._t("common.loading") : this._t("common.save")}
            </button>
          </div>
        </div>
      `;
    }
    const displayName = this._resolveName(preset);
    return html`
      <div class="card">
        <div class="card-head">
          <h4>${displayName}</h4>
          ${preset.is_system
            ? html`<span class="builtin-badge">${this._t("presets.builtin_badge")}</span>`
            : html`
              <div class="row-actions">
                <button class="icon" title=${this._t("presets.rename")}
                        @click=${() => this._startEdit(preset)}>✎</button>
                <button class="icon destructive" title=${this._t("presets.delete")}
                        @click=${() => this._onDelete(preset)}>×</button>
              </div>
            `}
        </div>
        ${preset.description
          ? html`<p class="desc">${preset.description}</p>`
          : ""}
      </div>
    `;
  }

  /** Resolve a preset's display name through i18n if its payload has a name_key. */
  _resolveName(preset) {
    const key = preset?.payload?.name_key;
    if (typeof key === "string" && key.length > 0) {
      const resolved = this._t(key);
      // i18n.t returns the key itself when no entry exists — fall back to name.
      if (resolved && resolved !== key) return resolved;
    }
    return preset.name;
  }

  render() {
    if (!this.open) return html``;
    return html`
      <melitta-modal .open=${this.open} .title=${this._t("presets.modal_title")}
                     @close=${() => this._close()}>
        ${this._error ? html`<div class="error">${this._error}</div>` : ""}
        ${this._presets.length === 0
          ? html`<p class="muted">${this._t("presets.empty")}</p>`
          : html`<div class="list">${this._presets.map((p) => this._renderPreset(p))}</div>`}
      </melitta-modal>
    `;
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        :host { display: contents; }
        .list { display: flex; flex-direction: column; gap: var(--mb-space-md); }
        .card {
          padding: var(--mb-space-md);
          border: 1px solid var(--divider-color);
          border-radius: var(--mb-radius-md);
          background: var(--secondary-background-color);
        }
        .card.editing {
          background: var(--card-background-color);
        }
        .card-head {
          display: flex; align-items: center; justify-content: space-between;
          gap: var(--mb-space-sm);
          margin-bottom: var(--mb-space-sm);
        }
        h4 { margin: 0; font-size: var(--mb-font-size-md); }
        .desc {
          margin: 0;
          color: var(--secondary-text-color);
          font-size: var(--mb-font-size-sm);
        }
        .row-actions { display: flex; gap: var(--mb-space-xs); }
        .builtin-badge {
          font-size: 11px;
          font-weight: 500;
          padding: 2px 8px;
          border-radius: 10px;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          text-transform: uppercase;
          letter-spacing: 0.4px;
        }
        .muted { color: var(--secondary-text-color); }
        .actions {
          display: flex; justify-content: flex-end; gap: var(--mb-space-sm);
        }
        .edit-name, .edit-desc {
          width: 100%;
          padding: var(--mb-space-sm);
          margin-bottom: var(--mb-space-sm);
          border: 1px solid var(--divider-color);
          border-radius: var(--mb-radius-sm);
          background: var(--primary-background-color);
          color: var(--primary-text-color);
          font-family: inherit;
          font-size: var(--mb-font-size-md);
          box-sizing: border-box;
        }
        .edit-desc { min-height: 60px; resize: vertical; }
        .error {
          background: rgba(244, 67, 54, 0.1); color: var(--error-color);
          padding: var(--mb-space-sm); border-radius: var(--mb-radius-sm);
          margin-bottom: var(--mb-space-md);
        }
        button {
          padding: var(--mb-space-sm) var(--mb-space-lg);
          border-radius: var(--mb-radius-sm);
          border: 1px solid var(--divider-color);
          background: transparent; color: var(--primary-text-color);
          font-size: var(--mb-font-size-md); cursor: pointer;
        }
        button:hover { background: var(--secondary-background-color); }
        button.primary {
          background: var(--primary-color); border-color: var(--primary-color);
          color: var(--text-primary-color, white);
        }
        button.ghost { color: var(--secondary-text-color); }
        button.icon {
          padding: 2px var(--mb-space-sm);
          font-size: var(--mb-font-size-md);
          line-height: 1.2;
          color: var(--secondary-text-color);
        }
        button.icon.destructive { color: var(--error-color); }
        button:disabled { opacity: 0.5; cursor: default; }
      `,
    ];
  }
}

if (!customElements.get("melitta-sommelier-presets")) {
  customElements.define("melitta-sommelier-presets", MelittaSommelierPresets);
}
