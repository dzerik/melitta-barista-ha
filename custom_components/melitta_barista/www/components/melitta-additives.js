/**
 * Add-ins manager: syrups, toppings, milk types.
 *
 * One unified add/edit modal with a type picker (syrup / topping / milk).
 * Inline tables show the existing entries with edit + delete controls.
 * Milk types use the existing sommelier-side single-list endpoint and only
 * carry a name; syrups and toppings have their own normalised tables and
 * also expose brand / notes.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const TYPES = ["syrup", "topping", "milk"];

class MelittaAdditives extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _syrups: { type: Array },
      _toppings: { type: Array },
      _milk: { type: Array },
      _editing: { type: Object },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._syrups = [];
    this._toppings = [];
    this._milk = [];
    this._editing = null;
    this._error = "";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadAll();
  }

  updated(changedProps) {
    if (changedProps.has("entryId") && this.entryId) this._loadAll();
  }

  async _loadAll() {
    try {
      const [s, tp, m] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/syrups/list" }),
        this.hass.callWS({ type: "melitta_barista/toppings/list" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/milk/get" }),
      ]);
      this._syrups = s.syrups || [];
      this._toppings = tp.toppings || [];
      this._milk = (m.milk_types || []).map((name, idx) => ({ id: name, name }));
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // ── modal ──

  _openAdd(typeOverride) {
    this._editing = {
      type: typeOverride || "syrup",
      id: null,
      name: "",
      brand: "",
      notes: "",
    };
  }

  _openEdit(type, item) {
    this._editing = { type, ...item };
  }

  _closeModal() { this._editing = null; }

  _updateField(key, value) {
    this._editing = { ...this._editing, [key]: value };
  }

  async _save() {
    const e = this._editing;
    if (!e?.name?.trim()) return;
    try {
      if (e.type === "milk") {
        // Milk: just a list of strings on the sommelier side.
        const newList = new Set(this._milk.map((m) => m.name));
        if (e.id && e.id !== e.name) newList.delete(e.id);
        newList.add(e.name.trim());
        await this.hass.callWS({
          type: "melitta_barista/sommelier/milk/set",
          milk_types: [...newList],
        });
      } else {
        const table = e.type === "syrup" ? "syrups" : "toppings";
        // Coerce DB NULLs to "" — voluptuous Optional(...): str rejects None.
        const fields = {
          name: e.name,
          brand: e.brand || "",
          notes: e.notes || "",
        };
        if (e.id) {
          // HA WS framework owns top-level "id" — see panel_api.py.
          await this.hass.callWS({
            type: `melitta_barista/${table}/update`,
            additive_id: e.id,
            ...fields,
          });
        } else {
          await this.hass.callWS({
            type: `melitta_barista/${table}/add`,
            ...fields,
          });
        }
      }
      this._closeModal();
      await this._loadAll();
    } catch (err) {
      this._error = err.message || String(err);
    }
  }

  async _delete(type, id) {
    if (!confirm("Delete?")) return;
    try {
      if (type === "milk") {
        await this.hass.callWS({
          type: "melitta_barista/sommelier/milk/set",
          milk_types: this._milk.filter((m) => m.name !== id).map((m) => m.name),
        });
      } else {
        const table = type === "syrup" ? "syrups" : "toppings";
        await this.hass.callWS({
          type: `melitta_barista/${table}/delete`,
          additive_id: id,
        });
      }
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _renderTable(type, rows, emptyKey) {
    if (rows.length === 0) {
      return html`<div class="hint">${this._t(emptyKey)}</div>`;
    }
    const showsBrand = type !== "milk";
    return html`
      <table>
        <thead><tr>
          <th>${this._t("additives.name")}</th>
          ${showsBrand ? html`<th>${this._t("additives.brand")}</th>` : ""}
          ${showsBrand ? html`<th>${this._t("additives.notes")}</th>` : ""}
          <th></th>
        </tr></thead>
        <tbody>
          ${rows.map((r) => html`
            <tr>
              <td>${r.name}</td>
              ${showsBrand ? html`<td>${r.brand || ""}</td>` : ""}
              ${showsBrand ? html`<td>${r.notes || ""}</td>` : ""}
              <td class="actions">
                <button class="icon edit" @click=${() => this._openEdit(type, r)}>✎</button>
                <button class="icon del" @click=${() => this._delete(type, r.id)}>×</button>
              </td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  _renderModal() {
    if (!this._editing) return "";
    const e = this._editing;
    const titleKey = e.id ? "modal.edit_additive" : "modal.add_additive";
    const showsBrand = e.type !== "milk";
    return html`
      <melitta-modal .open=${true} .title=${this._t(titleKey)}
        @close=${() => this._closeModal()}>
        <div class="form">
          <label>${this._t("modal.type")}
            <select .value=${e.type} ?disabled=${!!e.id}
              @change=${(ev) => this._updateField("type", ev.target.value)}>
              ${TYPES.map((tp) => html`
                <option value=${tp} ?selected=${tp === e.type}>
                  ${this._t(`modal.type.${tp}`)}
                </option>
              `)}
            </select>
          </label>
          <label>${this._t("additives.name")}
            <input type="text" .value=${e.name}
              @input=${(ev) => this._updateField("name", ev.target.value)} /></label>
          ${showsBrand ? html`
            <label>${this._t("additives.brand")}
              <input type="text" .value=${e.brand || ""}
                @input=${(ev) => this._updateField("brand", ev.target.value)} /></label>
            <label>${this._t("additives.notes")}
              <textarea rows="3"
                @input=${(ev) => this._updateField("notes", ev.target.value)}
              >${e.notes || ""}</textarea></label>
          ` : ""}
          <div class="form-actions">
            <button class="ghost" @click=${() => this._closeModal()}>${this._t("common.cancel")}</button>
            <button class="primary" @click=${() => this._save()}>${this._t("common.save")}</button>
          </div>
        </div>
      </melitta-modal>
    `;
  }

  render() {
    return html`
      <section class="card">
        <div class="head">
          <h2>${this._t("additives.title")}</h2>
          <button class="primary" @click=${() => this._openAdd()}>+ ${this._t("additives.add")}</button>
        </div>
        ${this._error ? html`<div class="error">${this._t("common.error")}: ${this._error}</div>` : ""}

        <h3>${this._t("additives.syrups")}</h3>
        ${this._renderTable("syrup", this._syrups, "additives.empty_syrups")}

        <h3>${this._t("additives.toppings")}</h3>
        ${this._renderTable("topping", this._toppings, "additives.empty_toppings")}

        <h3>${this._t("additives.milk")}</h3>
        ${this._renderTable("milk", this._milk, "additives.empty_milk")}

        ${this._renderModal()}
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
      .head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      h2 { margin: 0; font-size: 18px; }
      h3 {
        margin: 24px 0 8px;
        font-size: 14px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      table th {
        text-align: left; padding: 6px 8px;
        color: var(--secondary-text-color); font-weight: 500;
        border-bottom: 1px solid var(--divider-color);
      }
      table td {
        padding: 6px 8px;
        border-bottom: 1px solid var(--divider-color);
      }
      td.actions { text-align: right; white-space: nowrap; }
      button.icon {
        background: transparent;
        border: none;
        cursor: pointer;
        padding: 0 6px;
        font-size: 16px;
        line-height: 1;
      }
      button.icon.edit { color: var(--info-color, #2196f3); }
      button.icon.del { color: var(--error-color); font-size: 18px; }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }

      .form { display: flex; flex-direction: column; gap: 12px; }
      .form label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .form input, .form select, .form textarea {
        padding: 8px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 14px;
        font-family: inherit;
      }
      .form .form-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 4px;
      }
      button.primary {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.primary:hover { opacity: 0.9; }
      button.ghost {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
    `;
  }
}

customElements.define("melitta-additives", MelittaAdditives);
