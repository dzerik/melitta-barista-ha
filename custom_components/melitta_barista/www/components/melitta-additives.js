/**
 * Add-ins manager: syrups, toppings, milk types.
 *
 * Three tables, identical CRUD shape, sharing one tiny `_AdditiveTable`
 * helper. Milk types reuse the existing `sommelier/milk` endpoints
 * (single-string list); syrups and toppings have their own normalized
 * tables in the panel-side schema.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

class MelittaAdditives extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _syrups: { type: Array },
      _toppings: { type: Array },
      _milk: { type: Array },
      _newSyrup: { type: Object },
      _newTopping: { type: Object },
      _newMilk: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._syrups = [];
    this._toppings = [];
    this._milk = [];
    this._newSyrup = { name: "", brand: "", notes: "" };
    this._newTopping = { name: "", brand: "", notes: "" };
    this._newMilk = "";
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
      this._milk = m.milk_types || [];
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _addSyrup() {
    if (!this._newSyrup.name.trim()) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/syrups/add",
        ...this._newSyrup,
      });
      this._newSyrup = { name: "", brand: "", notes: "" };
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _addTopping() {
    if (!this._newTopping.name.trim()) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/toppings/add",
        ...this._newTopping,
      });
      this._newTopping = { name: "", brand: "", notes: "" };
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _addMilk() {
    const value = this._newMilk.trim();
    if (!value || this._milk.includes(value)) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/milk/set",
        milk_types: [...this._milk, value],
      });
      this._newMilk = "";
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _delete(table, id) {
    try {
      await this.hass.callWS({
        type: `melitta_barista/${table}/delete`,
        id,
      });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _deleteMilk(value) {
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/milk/set",
        milk_types: this._milk.filter((m) => m !== value),
      });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _renderTable(rows, table, emptyKey) {
    if (rows.length === 0) {
      return html`<div class="hint">${this._t(emptyKey)}</div>`;
    }
    return html`
      <table>
        <thead>
          <tr>
            <th>${this._t("additives.name")}</th>
            <th>${this._t("additives.brand")}</th>
            <th>${this._t("additives.notes")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => html`
            <tr>
              <td>${r.name}</td>
              <td>${r.brand || ""}</td>
              <td>${r.notes || ""}</td>
              <td><button class="del" @click=${() => this._delete(table, r.id)}>×</button></td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  _renderAddRow(state, fieldUpdate, addFn) {
    return html`
      <div class="add-row">
        <input
          type="text"
          .value=${state.name}
          placeholder=${this._t("additives.name")}
          @input=${(e) => fieldUpdate("name", e.target.value)}
        />
        <input
          type="text"
          .value=${state.brand}
          placeholder=${this._t("additives.brand")}
          @input=${(e) => fieldUpdate("brand", e.target.value)}
        />
        <input
          type="text"
          .value=${state.notes}
          placeholder=${this._t("additives.notes")}
          @input=${(e) => fieldUpdate("notes", e.target.value)}
        />
        <button class="add" @click=${addFn}>${this._t("additives.add")}</button>
      </div>
    `;
  }

  _renderMilk() {
    const items = this._milk;
    return html`
      ${items.length
        ? html`<div class="chips">
            ${items.map((m) => html`
              <span class="chip">
                ${m}
                <button class="chip-del" @click=${() => this._deleteMilk(m)}>×</button>
              </span>
            `)}
          </div>`
        : html`<div class="hint">${this._t("additives.empty_milk")}</div>`}
      <div class="add-row">
        <input
          type="text"
          .value=${this._newMilk}
          placeholder=${this._t("additives.name")}
          @input=${(e) => { this._newMilk = e.target.value; }}
          @keydown=${(e) => e.key === "Enter" && this._addMilk()}
        />
        <button class="add" @click=${() => this._addMilk()}>${this._t("additives.add")}</button>
      </div>
    `;
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("additives.title")}</h2>
        ${this._error ? html`<div class="error">${this._t("common.error")}: ${this._error}</div>` : ""}

        <h3>${this._t("additives.syrups")}</h3>
        ${this._renderTable(this._syrups, "syrups", "additives.empty_syrups")}
        ${this._renderAddRow(
          this._newSyrup,
          (k, v) => { this._newSyrup = { ...this._newSyrup, [k]: v }; },
          () => this._addSyrup(),
        )}

        <h3>${this._t("additives.toppings")}</h3>
        ${this._renderTable(this._toppings, "toppings", "additives.empty_toppings")}
        ${this._renderAddRow(
          this._newTopping,
          (k, v) => { this._newTopping = { ...this._newTopping, [k]: v }; },
          () => this._addTopping(),
        )}

        <h3>${this._t("additives.milk")}</h3>
        ${this._renderMilk()}
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
      .add-row {
        display: grid;
        grid-template-columns: 2fr 1fr 2fr auto;
        gap: 8px;
        margin: 8px 0 4px;
      }
      .add-row input {
        padding: 6px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
      }
      .add-row button.add {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      .add-row button.add:hover { opacity: 0.9; }
      button.del {
        background: transparent;
        border: none;
        color: var(--error-color);
        cursor: pointer;
        font-size: 18px;
        line-height: 1;
        padding: 0 6px;
      }
      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        padding: 4px 0;
      }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        background: var(--secondary-background-color);
        border-radius: 12px;
        padding: 4px 10px;
        font-size: 13px;
      }
      .chip-del {
        background: transparent;
        border: none;
        color: var(--secondary-text-color);
        cursor: pointer;
        font-size: 14px;
        line-height: 1;
        padding: 0;
      }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }
    `;
  }
}

customElements.define("melitta-additives", MelittaAdditives);
