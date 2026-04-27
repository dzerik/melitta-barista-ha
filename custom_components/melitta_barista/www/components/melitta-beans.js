/**
 * Coffee producers + beans manager — with LLM autofill.
 *
 * Producers: free-form CRUD against the panel-side `producers` table.
 * Beans: reuse existing sommelier_api endpoints
 * (`melitta_barista/sommelier/beans/{list,add,update,delete}`) plus the new
 * `melitta_barista/beans/autofill` which proxies brand+product to the HA
 * conversation agent and parses a JSON response into the bean schema.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const ROASTS = ["light", "medium", "medium_dark", "dark"];
const BEAN_TYPES = ["arabica", "arabica_robusta", "robusta"];
const ORIGINS = ["single_origin", "blend"];
const FLAVOR_NOTES = [
  "chocolate", "nutty", "fruity", "floral", "caramel",
  "spicy", "earthy", "honey", "berry", "citrus",
];

class MelittaBeans extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _producers: { type: Array },
      _beans: { type: Array },
      _newBean: { type: Object },
      _newProducer: { type: Object },
      _autofillRunning: { type: Boolean },
      _autofillRaw: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._producers = [];
    this._beans = [];
    this._newProducer = { name: "", country: "", website: "", notes: "" };
    this._newBean = this._emptyBean();
    this._autofillRunning = false;
    this._autofillRaw = "";
    this._error = "";
  }

  _emptyBean() {
    return {
      brand: "",
      product: "",
      roast: "medium",
      bean_type: "arabica",
      origin: "single_origin",
      origin_country: "",
      flavor_notes: [],
      composition: "",
    };
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
      const [p, b] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/producers/list" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/beans/list" }),
      ]);
      this._producers = p.producers || [];
      this._beans = b.beans || [];
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // ── producers ──

  async _addProducer() {
    if (!this._newProducer.name.trim()) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/producers/add",
        ...this._newProducer,
      });
      this._newProducer = { name: "", country: "", website: "", notes: "" };
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _deleteProducer(id) {
    try {
      await this.hass.callWS({ type: "melitta_barista/producers/delete", id });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // ── beans ──

  _updateBeanField(key, value) {
    this._newBean = { ...this._newBean, [key]: value };
  }

  async _addBean() {
    if (!this._newBean.brand.trim() || !this._newBean.product.trim()) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/beans/add",
        ...this._newBean,
      });
      this._newBean = this._emptyBean();
      this._autofillRaw = "";
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _deleteBean(id) {
    try {
      await this.hass.callWS({ type: "melitta_barista/sommelier/beans/delete", id });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _runAutofill() {
    const brand = this._newBean.brand.trim();
    const product = this._newBean.product.trim();
    if (!brand || !product) return;
    this._autofillRunning = true;
    this._autofillRaw = "";
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/beans/autofill",
        brand,
        product,
      });
      this._autofillRaw = result.raw || "";
      const parsed = result.parsed;
      if (parsed && typeof parsed === "object") {
        const merged = { ...this._newBean };
        if (ROASTS.includes(parsed.roast)) merged.roast = parsed.roast;
        if (BEAN_TYPES.includes(parsed.bean_type)) merged.bean_type = parsed.bean_type;
        if (ORIGINS.includes(parsed.origin)) merged.origin = parsed.origin;
        if (parsed.origin_country) merged.origin_country = parsed.origin_country;
        if (Array.isArray(parsed.flavor_notes)) {
          merged.flavor_notes = parsed.flavor_notes.filter((n) => FLAVOR_NOTES.includes(n));
        }
        if (parsed.composition) merged.composition = parsed.composition;
        this._newBean = merged;
      }
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._autofillRunning = false;
    }
  }

  _toggleFlavor(note) {
    const set = new Set(this._newBean.flavor_notes || []);
    set.has(note) ? set.delete(note) : set.add(note);
    this._updateBeanField("flavor_notes", [...set]);
  }

  // ── render ──

  _renderProducers() {
    return html`
      <h3>${this._t("beans.producers")}</h3>
      ${this._producers.length === 0
        ? html`<div class="hint">${this._t("beans.no_producers")}</div>`
        : html`
            <table>
              <thead>
                <tr>
                  <th>${this._t("beans.producer_name")}</th>
                  <th>${this._t("status.dis")}</th>
                  <th>${this._t("additives.notes")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${this._producers.map((p) => html`
                  <tr>
                    <td>${p.name}</td>
                    <td>${p.country || ""} ${p.website ? html`· <a href=${p.website} target="_blank">site</a>` : ""}</td>
                    <td>${p.notes || ""}</td>
                    <td><button class="del" @click=${() => this._deleteProducer(p.id)}>×</button></td>
                  </tr>
                `)}
              </tbody>
            </table>
          `}
      <div class="add-row">
        <input
          type="text"
          .value=${this._newProducer.name}
          placeholder=${this._t("beans.producer_name")}
          @input=${(e) => { this._newProducer = { ...this._newProducer, name: e.target.value }; }}
        />
        <input
          type="text"
          .value=${this._newProducer.country}
          placeholder=${this._t("beans.origin")}
          @input=${(e) => { this._newProducer = { ...this._newProducer, country: e.target.value }; }}
        />
        <input
          type="text"
          .value=${this._newProducer.website}
          placeholder="website"
          @input=${(e) => { this._newProducer = { ...this._newProducer, website: e.target.value }; }}
        />
        <button class="add" @click=${() => this._addProducer()}>${this._t("beans.add_producer")}</button>
      </div>
    `;
  }

  _renderBeansList() {
    if (this._beans.length === 0) {
      return html`<div class="hint">${this._t("beans.no_beans")}</div>`;
    }
    return html`
      <table>
        <thead>
          <tr>
            <th>${this._t("beans.producer_name")}</th>
            <th>${this._t("beans.bean_name")}</th>
            <th>${this._t("beans.roast")}</th>
            <th>${this._t("beans.origin")}</th>
            <th>${this._t("beans.notes")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${this._beans.map((b) => html`
            <tr>
              <td>${b.brand}</td>
              <td>${b.product}</td>
              <td>${b.roast}</td>
              <td>${b.origin_country || b.origin}</td>
              <td>${(b.flavor_notes || []).join(", ")}</td>
              <td><button class="del" @click=${() => this._deleteBean(b.id)}>×</button></td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  _renderBeanForm() {
    const b = this._newBean;
    return html`
      <h3>${this._t("beans.add_bean")}</h3>
      <div class="bean-form">
        <input type="text" .value=${b.brand}
          placeholder=${this._t("beans.producer_name")}
          @input=${(e) => this._updateBeanField("brand", e.target.value)} />
        <input type="text" .value=${b.product}
          placeholder=${this._t("beans.bean_name")}
          @input=${(e) => this._updateBeanField("product", e.target.value)} />
        <button class="autofill"
          ?disabled=${this._autofillRunning || !b.brand || !b.product}
          @click=${() => this._runAutofill()}>
          ${this._autofillRunning ? this._t("beans.autofill_running") : this._t("beans.autofill")}
        </button>

        <select .value=${b.roast}
          @change=${(e) => this._updateBeanField("roast", e.target.value)}>
          ${ROASTS.map((r) => html`<option value=${r}>${r}</option>`)}
        </select>
        <select .value=${b.bean_type}
          @change=${(e) => this._updateBeanField("bean_type", e.target.value)}>
          ${BEAN_TYPES.map((bt) => html`<option value=${bt}>${bt}</option>`)}
        </select>
        <select .value=${b.origin}
          @change=${(e) => this._updateBeanField("origin", e.target.value)}>
          ${ORIGINS.map((o) => html`<option value=${o}>${o}</option>`)}
        </select>

        <input type="text" .value=${b.origin_country}
          placeholder=${this._t("beans.origin")}
          @input=${(e) => this._updateBeanField("origin_country", e.target.value)} />
        <input type="text" .value=${b.composition}
          placeholder="composition"
          @input=${(e) => this._updateBeanField("composition", e.target.value)} />

        <div class="flavors">
          ${FLAVOR_NOTES.map((n) => html`
            <label class=${b.flavor_notes.includes(n) ? "flavor on" : "flavor"}>
              <input type="checkbox"
                .checked=${b.flavor_notes.includes(n)}
                @change=${() => this._toggleFlavor(n)} />
              ${n}
            </label>
          `)}
        </div>

        ${this._autofillRaw ? html`
          <details class="raw">
            <summary>LLM raw response</summary>
            <pre>${this._autofillRaw}</pre>
          </details>
        ` : ""}

        <button class="add wide" @click=${() => this._addBean()}>${this._t("common.save")}</button>
      </div>
    `;
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("beans.title")}</h2>
        ${this._error ? html`<div class="error">${this._t("common.error")}: ${this._error}</div>` : ""}
        ${this._renderProducers()}
        <h3>${this._t("beans.beans")}</h3>
        ${this._renderBeansList()}
        ${this._renderBeanForm()}
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
      .bean-form {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 8px;
        background: var(--secondary-background-color);
        padding: 12px;
        border-radius: 6px;
        margin-top: 8px;
      }
      .bean-form input, .bean-form select {
        padding: 6px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
      }
      .bean-form .flavors {
        grid-column: 1 / -1;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .flavor {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 10px;
        border-radius: 12px;
        background: var(--primary-background-color);
        border: 1px solid var(--divider-color);
        font-size: 12px;
        cursor: pointer;
      }
      .flavor.on {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border-color: var(--primary-color);
      }
      .flavor input { display: none; }
      button.add, button.autofill {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.autofill { background: var(--info-color, #2196f3); }
      button.add.wide, button.autofill {
        grid-column: 1 / -1;
      }
      button:disabled { opacity: 0.5; cursor: not-allowed; }
      button.add:hover:not(:disabled), button.autofill:hover:not(:disabled) { opacity: 0.9; }
      button.del {
        background: transparent;
        border: none;
        color: var(--error-color);
        cursor: pointer;
        font-size: 18px;
        line-height: 1;
        padding: 0 6px;
      }
      details.raw {
        grid-column: 1 / -1;
        background: var(--primary-background-color);
        padding: 8px;
        border-radius: 4px;
        font-size: 12px;
      }
      details.raw pre {
        white-space: pre-wrap;
        word-break: break-word;
        margin: 8px 0 0;
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

customElements.define("melitta-beans", MelittaBeans);
