/**
 * Coffee producers + beans manager.
 *
 * - Producers: dropdown-source for the bean form. CRUD via modal.
 * - Beans: CRUD via modal. Producer is a `<select>` populated from
 *   `_producers`; flavor notes are dynamic chips backed by the
 *   `melitta_barista/tags/*` endpoints (any new typed value is upserted).
 * - Hopper assignment: per-machine bean → hopper widget using the existing
 *   sommelier hopper endpoints; users can mark which bean is currently
 *   loaded into each grinder slot.
 * - LLM autofill: same plumbing as before, but the LLM is asked for free-form
 *   tags (no hardcoded list); the editor merges those into the available
 *   tags pool so they're picklable later for other beans.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const ROASTS = ["light", "medium", "medium_dark", "dark"];
const BEAN_TYPES = ["arabica", "arabica_robusta", "robusta"];
const ORIGINS = ["single_origin", "blend"];

class MelittaBeans extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _producers: { type: Array },
      _beans: { type: Array },
      _allTags: { type: Array },
      _hoppers: { type: Object },
      _editingBean: { type: Object },
      _editingProducer: { type: Object },
      _autofillRunning: { type: Boolean },
      _autofillRaw: { type: String },
      _autofillVia: { type: String },
      _autofillErrors: { type: Array },
      _newTag: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._producers = [];
    this._beans = [];
    this._allTags = [];
    this._hoppers = { hopper1: null, hopper2: null };
    this._editingBean = null;
    this._editingProducer = null;
    this._autofillRunning = false;
    this._autofillRaw = "";
    this._autofillVia = "";
    this._autofillErrors = [];
    this._newTag = "";
    this._error = "";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  _emptyBean() {
    return {
      id: null,
      brand: this._producers[0]?.name || "",
      product: "",
      roast: "medium",
      bean_type: "arabica",
      origin: "single_origin",
      origin_country: "",
      flavor_notes: [],
      composition: "",
    };
  }

  _emptyProducer() {
    return { id: null, name: "", country: "", website: "", notes: "" };
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
      const [p, b, tags, hoppers] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/producers/list" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/beans/list" }),
        this.hass.callWS({ type: "melitta_barista/tags/list" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/hoppers/get" }),
      ]);
      this._producers = p.producers || [];
      this._beans = b.beans || [];
      this._allTags = tags.tags || [];
      this._hoppers = hoppers || { hopper1: null, hopper2: null };
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // ── producers ──

  _openAddProducer() { this._editingProducer = this._emptyProducer(); }
  _openEditProducer(p) { this._editingProducer = { ...p }; }
  _closeProducerModal() { this._editingProducer = null; }

  async _saveProducer() {
    const p = this._editingProducer;
    if (!p?.name?.trim()) return;
    try {
      if (p.id) {
        await this.hass.callWS({ type: "melitta_barista/producers/update", ...p });
      } else {
        await this.hass.callWS({
          type: "melitta_barista/producers/add",
          name: p.name, country: p.country, website: p.website, notes: p.notes,
        });
      }
      this._closeProducerModal();
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _deleteProducer(id) {
    if (!confirm("Delete?")) return;
    try {
      await this.hass.callWS({ type: "melitta_barista/producers/delete", id });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // ── beans ──

  _openAddBean() {
    if (this._producers.length === 0) {
      this._error = this._t("beans.no_producers");
      return;
    }
    this._editingBean = this._emptyBean();
    this._autofillRaw = "";
  }
  _openEditBean(b) {
    this._editingBean = { ...b, flavor_notes: [...(b.flavor_notes || [])] };
    this._autofillRaw = "";
  }
  _closeBeanModal() { this._editingBean = null; this._autofillRaw = ""; }

  _updateBeanField(key, value) {
    this._editingBean = { ...this._editingBean, [key]: value };
  }

  async _saveBean() {
    const b = this._editingBean;
    if (!b?.brand?.trim() || !b?.product?.trim()) return;
    try {
      // Upsert any new tags into the global tag list so they show in
      // autocomplete suggestions for future beans.
      const newTags = (b.flavor_notes || []).filter((tag) => !this._allTags.includes(tag));
      for (const tag of newTags) {
        await this.hass.callWS({ type: "melitta_barista/tags/add", name: tag });
      }
      const payload = b.id
        ? { type: "melitta_barista/sommelier/beans/update", id: b.id, ...b }
        : { type: "melitta_barista/sommelier/beans/add", ...b };
      delete payload.id; // for add path; the update path keeps id via spread
      if (b.id) payload.id = b.id;
      await this.hass.callWS(payload);
      this._closeBeanModal();
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _deleteBean(id) {
    if (!confirm("Delete?")) return;
    try {
      await this.hass.callWS({ type: "melitta_barista/sommelier/beans/delete", id });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _runAutofill() {
    const brand = this._editingBean.brand.trim();
    const product = this._editingBean.product.trim();
    if (!brand || !product) return;
    this._autofillRunning = true;
    this._autofillRaw = "";
    this._autofillErrors = [];
    this._autofillVia = "";
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/beans/autofill",
        brand,
        product,
      });
      this._autofillRaw = result.raw || "";
      this._autofillVia = result.via || "";
      this._autofillErrors = result.validation_errors || [];
      const parsed = result.parsed;
      if (parsed && typeof parsed === "object") {
        const merged = { ...this._editingBean };
        if (ROASTS.includes(parsed.roast)) merged.roast = parsed.roast;
        if (BEAN_TYPES.includes(parsed.bean_type)) merged.bean_type = parsed.bean_type;
        if (ORIGINS.includes(parsed.origin)) merged.origin = parsed.origin;
        if (parsed.origin_country) merged.origin_country = parsed.origin_country;
        if (Array.isArray(parsed.flavor_notes)) {
          // Dynamic: accept any string the LLM emits, normalise & dedupe.
          const cleaned = [...new Set(parsed.flavor_notes
            .filter((n) => typeof n === "string" && n.trim())
            .map((n) => n.trim().toLowerCase())
          )];
          merged.flavor_notes = cleaned;
        }
        if (parsed.composition) merged.composition = parsed.composition;
        this._editingBean = merged;
      }
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._autofillRunning = false;
    }
  }

  // ── tags (chips) ──

  _addTagToBean(tag) {
    const value = tag.trim().toLowerCase();
    if (!value) return;
    const set = new Set(this._editingBean.flavor_notes || []);
    if (set.has(value)) return;
    set.add(value);
    this._updateBeanField("flavor_notes", [...set]);
    this._newTag = "";
  }

  _removeTagFromBean(tag) {
    const set = new Set(this._editingBean.flavor_notes || []);
    set.delete(tag);
    this._updateBeanField("flavor_notes", [...set]);
  }

  _onTagKeyDown(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      this._addTagToBean(this._newTag);
    }
  }

  // ── hopper assignment ──

  async _assignHopper(hopperId, beanId) {
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/hoppers/assign",
        hopper_id: hopperId,
        bean_id: beanId || null,
      });
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _renderHopperRow(label, hopperId, current) {
    return html`
      <div class="row">
        <span class="label">${label}:</span>
        <select
          class="value"
          .value=${current?.bean?.id || ""}
          @change=${(e) => this._assignHopper(hopperId, e.target.value)}
        >
          <option value="">${this._t("hopper.unassigned")}</option>
          ${this._beans.map((b) => html`
            <option value=${b.id}>${b.brand} — ${b.product}</option>
          `)}
        </select>
      </div>
    `;
  }

  // ── render ──

  _renderProducersTable() {
    if (this._producers.length === 0) {
      return html`<div class="hint">${this._t("beans.no_producers")}</div>`;
    }
    return html`
      <table>
        <thead><tr>
          <th>${this._t("beans.producer_name")}</th>
          <th>${this._t("beans.origin")}</th>
          <th></th>
        </tr></thead>
        <tbody>
          ${this._producers.map((p) => html`
            <tr>
              <td>${p.name}</td>
              <td>${p.country || ""}${p.website ? html` · <a href=${p.website} target="_blank">site</a>` : ""}</td>
              <td class="actions">
                <button class="icon edit" @click=${() => this._openEditProducer(p)}>✎</button>
                <button class="icon del" @click=${() => this._deleteProducer(p.id)}>×</button>
              </td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  _renderBeansTable() {
    if (this._beans.length === 0) {
      return html`<div class="hint">${this._t("beans.no_beans")}</div>`;
    }
    return html`
      <table>
        <thead><tr>
          <th>${this._t("beans.producer_name")}</th>
          <th>${this._t("beans.bean_name")}</th>
          <th>${this._t("beans.roast")}</th>
          <th>${this._t("beans.origin")}</th>
          <th>${this._t("tags.title")}</th>
          <th></th>
        </tr></thead>
        <tbody>
          ${this._beans.map((b) => html`
            <tr>
              <td>${b.brand}</td>
              <td>${b.product}</td>
              <td>${b.roast}</td>
              <td>${b.origin_country || b.origin}</td>
              <td>${(b.flavor_notes || []).join(", ")}</td>
              <td class="actions">
                <button class="icon edit" @click=${() => this._openEditBean(b)}>✎</button>
                <button class="icon del" @click=${() => this._deleteBean(b.id)}>×</button>
              </td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  _renderProducerModal() {
    if (!this._editingProducer) return "";
    const p = this._editingProducer;
    const titleKey = p.id ? "modal.edit_producer" : "modal.add_producer";
    return html`
      <melitta-modal .open=${true} .title=${this._t(titleKey)}
        @close=${() => this._closeProducerModal()}>
        <div class="form">
          <label>${this._t("beans.producer_name")}
            <input type="text" .value=${p.name}
              @input=${(e) => { this._editingProducer = { ...p, name: e.target.value }; }} /></label>
          <label>${this._t("beans.origin")}
            <input type="text" .value=${p.country}
              @input=${(e) => { this._editingProducer = { ...p, country: e.target.value }; }} /></label>
          <label>website
            <input type="text" .value=${p.website}
              @input=${(e) => { this._editingProducer = { ...p, website: e.target.value }; }} /></label>
          <label>${this._t("beans.notes")}
            <textarea rows="3"
              @input=${(e) => { this._editingProducer = { ...p, notes: e.target.value }; }}
            >${p.notes || ""}</textarea></label>
          <div class="form-actions">
            <button class="ghost" @click=${() => this._closeProducerModal()}>${this._t("common.cancel")}</button>
            <button class="primary" @click=${() => this._saveProducer()}>${this._t("common.save")}</button>
          </div>
        </div>
      </melitta-modal>
    `;
  }

  _renderBeanModal() {
    if (!this._editingBean) return "";
    const b = this._editingBean;
    const titleKey = b.id ? "modal.edit_bean" : "modal.add_bean";
    const tagSuggestions = this._allTags.filter((tag) => !(b.flavor_notes || []).includes(tag));
    return html`
      <melitta-modal .open=${true} .title=${this._t(titleKey)}
        @close=${() => this._closeBeanModal()}>
        <div class="form">
          <label>${this._t("beans.producer_name")}
            <select .value=${b.brand}
              @change=${(e) => this._updateBeanField("brand", e.target.value)}>
              ${this._producers.map((p) => html`
                <option value=${p.name} ?selected=${p.name === b.brand}>${p.name}</option>
              `)}
            </select>
          </label>
          <label>${this._t("beans.bean_name")}
            <input type="text" .value=${b.product}
              @input=${(e) => this._updateBeanField("product", e.target.value)} /></label>

          <button class="autofill"
            ?disabled=${this._autofillRunning || !b.brand || !b.product}
            @click=${() => this._runAutofill()}>
            ${this._autofillRunning ? this._t("beans.autofill_running") : this._t("beans.autofill")}
          </button>

          <div class="grid3">
            <label>${this._t("beans.roast")}
              <select .value=${b.roast}
                @change=${(e) => this._updateBeanField("roast", e.target.value)}>
                ${ROASTS.map((r) => html`<option value=${r} ?selected=${r === b.roast}>${r}</option>`)}
              </select></label>
            <label>${this._t("modal.type")}
              <select .value=${b.bean_type}
                @change=${(e) => this._updateBeanField("bean_type", e.target.value)}>
                ${BEAN_TYPES.map((bt) => html`<option value=${bt} ?selected=${bt === b.bean_type}>${bt}</option>`)}
              </select></label>
            <label>${this._t("beans.origin")}
              <select .value=${b.origin}
                @change=${(e) => this._updateBeanField("origin", e.target.value)}>
                ${ORIGINS.map((o) => html`<option value=${o} ?selected=${o === b.origin}>${o}</option>`)}
              </select></label>
          </div>

          <label>${this._t("beans.origin")} (country)
            <input type="text" .value=${b.origin_country}
              @input=${(e) => this._updateBeanField("origin_country", e.target.value)} /></label>
          <label>${this._t("beans.notes")} / composition
            <input type="text" .value=${b.composition}
              @input=${(e) => this._updateBeanField("composition", e.target.value)} /></label>

          <fieldset class="tags">
            <legend>${this._t("tags.title")}</legend>
            <div class="chips">
              ${(b.flavor_notes || []).map((tag) => html`
                <span class="chip on">
                  ${tag}
                  <button class="chip-del" @click=${() => this._removeTagFromBean(tag)}>×</button>
                </span>
              `)}
            </div>
            <div class="tag-add">
              <input type="text"
                list="tag-suggestions"
                .value=${this._newTag}
                placeholder=${this._t("tags.add_placeholder")}
                @input=${(e) => { this._newTag = e.target.value; }}
                @keydown=${(e) => this._onTagKeyDown(e)} />
              <datalist id="tag-suggestions">
                ${tagSuggestions.map((tag) => html`<option value=${tag}></option>`)}
              </datalist>
              <button class="add" @click=${() => this._addTagToBean(this._newTag)}>+</button>
            </div>
          </fieldset>

          ${this._autofillVia ? html`
            <div class="via-label">via: <code>${this._autofillVia}</code></div>
          ` : ""}
          ${this._autofillErrors && this._autofillErrors.length ? html`
            <div class="validation-errors">
              <strong>Validation errors:</strong>
              <ul>
                ${this._autofillErrors.map((err) => html`
                  <li><code>${err.loc}</code>: ${err.msg}</li>
                `)}
              </ul>
            </div>
          ` : ""}
          ${this._autofillRaw ? html`
            <details class="raw">
              <summary>LLM raw response</summary>
              <pre>${this._autofillRaw}</pre>
            </details>
          ` : ""}

          <div class="form-actions">
            <button class="ghost" @click=${() => this._closeBeanModal()}>${this._t("common.cancel")}</button>
            <button class="primary" @click=${() => this._saveBean()}>${this._t("common.save")}</button>
          </div>
        </div>
      </melitta-modal>
    `;
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("beans.title")}</h2>
        ${this._error ? html`<div class="error">${this._t("common.error")}: ${this._error}</div>` : ""}

        <div class="section-head">
          <h3>${this._t("beans.producers")}</h3>
          <button class="primary small" @click=${() => this._openAddProducer()}>
            + ${this._t("beans.add_producer")}
          </button>
        </div>
        ${this._renderProducersTable()}

        <div class="section-head">
          <h3>${this._t("beans.beans")}</h3>
          <button class="primary small" @click=${() => this._openAddBean()}>
            + ${this._t("beans.add_bean")}
          </button>
        </div>
        ${this._renderBeansTable()}

        <h3>${this._t("hopper.title")}</h3>
        <div class="grid">
          ${this._renderHopperRow(this._t("hopper.left"), 1, this._hoppers.hopper1)}
          ${this._renderHopperRow(this._t("hopper.right"), 2, this._hoppers.hopper2)}
        </div>

        ${this._renderProducerModal()}
        ${this._renderBeanModal()}
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
        margin: 0;
        font-size: 14px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .section-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin: 24px 0 8px;
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

      .grid { display: flex; flex-direction: column; gap: 2px; }
      .row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 4px 0;
        border-bottom: 1px solid var(--divider-color);
        font-size: 13px;
      }
      .row .label {
        color: var(--secondary-text-color);
        flex: 0 0 auto;
        white-space: nowrap;
      }
      .row select.value {
        flex: 1;
        max-width: 360px;
      }

      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }

      /* form (used inside modal) */
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
      .form .grid3 {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 8px;
      }
      .form fieldset.tags {
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        padding: 8px 12px;
      }
      .form fieldset.tags legend { padding: 0 4px; font-size: 12px; color: var(--secondary-text-color); }
      .form .form-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 4px;
      }

      /* chips */
      .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 4px 0; }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 10px;
        border-radius: 12px;
        background: var(--primary-color);
        color: var(--text-primary-color);
        font-size: 12px;
      }
      .chip-del {
        background: transparent;
        border: none;
        color: inherit;
        cursor: pointer;
        font-size: 14px;
        line-height: 1;
        padding: 0;
      }
      .tag-add {
        display: flex;
        gap: 6px;
        margin-top: 8px;
      }
      .tag-add input {
        flex: 1;
        padding: 6px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
      }
      .tag-add button.add {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }

      /* buttons */
      button.primary, button.autofill {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.primary.small { padding: 4px 10px; font-size: 12px; }
      button.autofill { background: var(--info-color, #2196f3); }
      button.primary:hover:not(:disabled), button.autofill:hover:not(:disabled) { opacity: 0.9; }
      button:disabled { opacity: 0.5; cursor: not-allowed; }

      button.ghost {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }

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

      details.raw {
        background: var(--secondary-background-color);
        padding: 8px;
        border-radius: 4px;
        font-size: 12px;
      }
      details.raw pre {
        white-space: pre-wrap;
        word-break: break-word;
        margin: 8px 0 0;
      }
      .via-label {
        font-size: 11px;
        color: var(--secondary-text-color);
      }
      .via-label code {
        background: var(--primary-background-color);
        padding: 1px 4px;
        border-radius: 3px;
      }
      .validation-errors {
        background: var(--warning-color, #ff9800);
        color: var(--text-primary-color);
        border-radius: 4px;
        padding: 8px 12px;
        font-size: 12px;
      }
      .validation-errors ul { margin: 4px 0 0 16px; padding: 0; }
      .validation-errors code {
        background: rgba(0, 0, 0, 0.15);
        padding: 1px 4px;
        border-radius: 3px;
      }
    `;
  }
}

customElements.define("melitta-beans", MelittaBeans);
