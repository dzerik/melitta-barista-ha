/**
 * Add-ins manager: syrups, toppings, milk types.
 *
 * One unified add/edit modal with a type picker (syrup / topping / milk).
 * Inline tables show the existing entries with edit + delete controls.
 * Milk types use the existing sommelier-side single-list endpoint and only
 * carry a name; syrups and toppings have their own normalised tables and
 * also expose brand / notes plus the P8a-introduced rich fields
 * (producer / variant / flavor_notes / composition / attributes).
 *
 * Syrups & toppings additionally support a "Fill from LLM" button that
 * routes to the P8b backend autofill endpoint and merges the parsed
 * response into the editing state.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n/index.js";
import "./melitta-confirm.js";

const TYPES = ["syrup", "topping", "milk"];
const ATTRIBUTE_KEYS = [
  "vegan",
  "sugar_free",
  "lactose_free",
  "gluten_free",
  "nut_free",
];

class MelittaAdditives extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _syrups: { type: Array },
      _toppings: { type: Array },
      _milk: { type: Array },
      _producers: { type: Array },
      _editing: { type: Object },
      _error: { type: String },
      _autofillBusy: { type: Boolean },
      _autofillError: { type: String },
      _newFlavorNote: { type: String },
    };
  }

  constructor() {
    super();
    this._syrups = [];
    this._toppings = [];
    this._milk = [];
    this._producers = [];
    this._editing = null;
    this._error = "";
    this._autofillBusy = false;
    this._autofillError = "";
    this._newFlavorNote = "";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  /**
   * Open <melitta-confirm> and await user decision.
   * Returns true if the user confirmed, false otherwise.
   */
  async _confirmDelete(itemLabel) {
    let dialog = this.renderRoot.querySelector("melitta-confirm");
    if (!dialog) {
      dialog = document.createElement("melitta-confirm");
      this.renderRoot.appendChild(dialog);
    }
    return dialog.ask({
      title: this._t("confirm.delete.title"),
      message: itemLabel
        ? `${this._t("common.delete_confirm")} — ${itemLabel}`
        : this._t("common.delete_confirm"),
      confirmLabel: this._t("confirm.delete.confirm"),
      cancelLabel: this._t("common.cancel"),
      destructive: true,
    });
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
      const [s, tp, m, p] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/syrups/list" }),
        this.hass.callWS({ type: "melitta_barista/toppings/list" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/milk/get" }),
        this.hass.callWS({ type: "melitta_barista/producers/list" }).catch(() => ({ producers: [] })),
      ]);
      this._syrups = s.syrups || [];
      this._toppings = tp.toppings || [];
      this._milk = (m.milk_types || []).map((name, idx) => ({ id: name, name }));
      this._producers = p.producers || [];
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
      producer_id: null,
      variant: "",
      flavor_notes: [],
      composition: "",
      attributes: {},
    };
    this._autofillError = "";
    this._newFlavorNote = "";
  }

  _openEdit(type, item) {
    this._editing = {
      type,
      ...item,
      flavor_notes: Array.isArray(item.flavor_notes) ? [...item.flavor_notes] : [],
      attributes:
        item.attributes && typeof item.attributes === "object"
          ? { ...item.attributes }
          : {},
    };
    this._autofillError = "";
    this._newFlavorNote = "";
  }

  _closeModal() {
    this._editing = null;
    this._autofillError = "";
    this._newFlavorNote = "";
  }

  _updateField(key, value) {
    this._editing = { ...this._editing, [key]: value };
  }

  // ── flavor-note chips ──

  _addFlavorNote(raw) {
    const value = (raw || "").trim();
    if (!value) return;
    const existing = this._editing.flavor_notes || [];
    if (existing.includes(value)) {
      this._newFlavorNote = "";
      return;
    }
    this._updateField("flavor_notes", [...existing, value]);
    this._newFlavorNote = "";
  }

  _removeFlavorNote(note) {
    const next = (this._editing.flavor_notes || []).filter((n) => n !== note);
    this._updateField("flavor_notes", next);
  }

  _onFlavorNoteKeyDown(ev) {
    if (ev.key === "Enter") {
      ev.preventDefault();
      this._addFlavorNote(this._newFlavorNote);
    }
  }

  // ── attribute chips ──

  _toggleAttribute(key) {
    const current = this._editing.attributes || {};
    const next = { ...current };
    if (next[key]) {
      delete next[key];
    } else {
      next[key] = true;
    }
    this._updateField("attributes", next);
  }

  // ── Fill from LLM ──

  async _runAutofill() {
    const e = this._editing;
    if (!e || !e.brand || !e.brand.trim()) {
      this._autofillError = this._t("additives.fill_needs_brand");
      return;
    }
    if (e.type === "milk") return;
    const table = e.type === "syrup" ? "syrups" : "toppings";
    this._autofillBusy = true;
    this._autofillError = "";
    try {
      const payload = {
        type: `melitta_barista/${table}/autofill`,
        brand: e.brand.trim(),
      };
      if (e.variant && e.variant.trim()) {
        payload.variant = e.variant.trim();
      }
      const result = await this.hass.callWS(payload);
      const parsed = result && result.parsed;
      if (parsed && typeof parsed === "object") {
        const merged = { ...this._editing };
        if (Array.isArray(parsed.flavor_notes)) {
          const cleaned = [...new Set(
            parsed.flavor_notes
              .filter((n) => typeof n === "string" && n.trim())
              .map((n) => n.trim())
          )];
          merged.flavor_notes = cleaned;
        }
        if (typeof parsed.composition === "string" && parsed.composition.trim()) {
          merged.composition = parsed.composition;
        }
        if (parsed.attributes && typeof parsed.attributes === "object") {
          // Only keep boolean-true keys to keep the editing dict tidy.
          const attrs = {};
          for (const [k, v] of Object.entries(parsed.attributes)) {
            if (v === true) attrs[k] = true;
          }
          merged.attributes = attrs;
        }
        if (typeof parsed.variant === "string" && parsed.variant.trim() && !merged.variant) {
          merged.variant = parsed.variant.trim();
        }
        this._editing = merged;
      }
    } catch (err) {
      this._autofillError = err.message
        ? `${this._t("additives.fill_failed")} ${err.message}`
        : this._t("additives.fill_failed");
      // eslint-disable-next-line no-console
      console.warn("additive autofill failed", err);
    } finally {
      this._autofillBusy = false;
    }
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
        // Send rich fields only when set so partial-patch semantics on
        // the backend keep prior values intact.
        if (e.producer_id != null && e.producer_id !== "") {
          fields.producer_id = e.producer_id;
        }
        if (e.variant) fields.variant = e.variant;
        if (Array.isArray(e.flavor_notes) && e.flavor_notes.length) {
          fields.flavor_notes = e.flavor_notes;
        }
        if (e.composition) fields.composition = e.composition;
        if (e.attributes && Object.keys(e.attributes).length) {
          fields.attributes = e.attributes;
        }
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

  async _toggleAvailable(type, item) {
    // milk rows are a flat string list — no per-item stock flag in P4a.
    if (type === "milk") return;
    const table = type === "syrup" ? "syrups" : "toppings";
    const next = !(item.available ?? 1);
    try {
      await this.hass.callWS({
        type: `melitta_barista/${table}/set_available`,
        additive_id: item.id,
        available: next,
      });
      this._error = "";
      await this._loadAll();
    } catch (err) {
      this._error = this._t("additives.toggle_stock_failed");
      // Keep raw cause discoverable in the console for diagnostics.
      // eslint-disable-next-line no-console
      console.warn("set_available failed", err);
    }
  }

  async _delete(type, id) {
    const list =
      type === "syrup" ? this._syrups : type === "topping" ? this._toppings : this._milk;
    const item = list.find((x) => x.id === id);
    if (!(await this._confirmDelete(item?.name))) return;
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
          ${rows.map((r) => {
            const inStock = (r.available ?? 1) ? true : false;
            const rowClass = type !== "milk" && !inStock ? "dimmed" : "";
            return html`
            <tr class=${rowClass}>
              <td>${r.name}</td>
              ${showsBrand ? html`<td>${r.brand || ""}</td>` : ""}
              ${showsBrand ? html`<td>${r.notes || ""}</td>` : ""}
              <td class="actions">
                ${type !== "milk" ? html`
                  <button
                    class="icon stock ${inStock ? "in-stock" : "out-of-stock"}"
                    title=${inStock ? this._t("additives.in_stock") : this._t("additives.out_of_stock")}
                    @click=${() => this._toggleAvailable(type, r)}
                  >${inStock ? "✓" : "○"}</button>
                ` : ""}
                <button class="icon edit" @click=${() => this._openEdit(type, r)}>✎</button>
                <button class="icon del" @click=${() => this._delete(type, r.id)}>×</button>
              </td>
            </tr>
          `;
          })}
        </tbody>
      </table>
    `;
  }

  _renderRichFields(e) {
    const attrs = e.attributes || {};
    const notes = e.flavor_notes || [];
    return html`
      <button
        class="fill-llm-button"
        ?disabled=${this._autofillBusy || !e.brand || !e.brand.trim()}
        @click=${() => this._runAutofill()}
        title=${(!e.brand || !e.brand.trim()) ? this._t("additives.fill_needs_brand") : ""}
      >
        ${this._autofillBusy ? "…" : "✨"} ${this._t("additives.fill_from_llm")}
      </button>
      ${this._autofillError ? html`
        <div class="autofill-error">${this._autofillError}</div>
      ` : ""}

      <label>${this._t("additives.producer")}
        <select
          .value=${e.producer_id == null ? "" : String(e.producer_id)}
          @change=${(ev) => {
            const v = ev.target.value;
            this._updateField("producer_id", v === "" ? null : Number(v));
          }}>
          <option value="" ?selected=${e.producer_id == null}>
            ${this._t("additives.producer_none")}
          </option>
          ${this._producers.map((p) => html`
            <option value=${p.id} ?selected=${String(p.id) === String(e.producer_id)}>
              ${p.name}
            </option>
          `)}
        </select>
      </label>

      <label>${this._t("additives.variant")}
        <input type="text" .value=${e.variant || ""}
          @input=${(ev) => this._updateField("variant", ev.target.value)} />
      </label>

      <fieldset class="rich-group">
        <legend>${this._t("additives.flavor_notes")}</legend>
        <div class="chip-row">
          ${notes.map((n) => html`
            <button
              type="button"
              class="chip removable"
              @click=${() => this._removeFlavorNote(n)}
              title=${n}
            >${n} <span class="chip-x">×</span></button>
          `)}
        </div>
        <div class="chip-add">
          <input type="text"
            .value=${this._newFlavorNote}
            placeholder=${this._t("additives.flavor_notes_add")}
            @input=${(ev) => { this._newFlavorNote = ev.target.value; }}
            @keydown=${(ev) => this._onFlavorNoteKeyDown(ev)} />
          <button type="button" class="chip-add-btn"
            @click=${() => this._addFlavorNote(this._newFlavorNote)}>+</button>
        </div>
      </fieldset>

      <label>${this._t("additives.composition")}
        <textarea rows="3"
          .value=${e.composition || ""}
          @input=${(ev) => this._updateField("composition", ev.target.value)}
        >${e.composition || ""}</textarea>
      </label>

      <fieldset class="rich-group">
        <legend>${this._t("additives.attributes")}</legend>
        <div class="chip-row">
          ${ATTRIBUTE_KEYS.map((k) => html`
            <button
              type="button"
              class="chip toggle ${attrs[k] ? "active" : ""}"
              @click=${() => this._toggleAttribute(k)}
            >${this._t(`additives.attr.${k}`)}</button>
          `)}
        </div>
      </fieldset>
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
            ${this._renderRichFields(e)}
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
      button.icon.stock { font-size: 14px; }
      button.icon.stock.in-stock { color: var(--success-color, #4caf50); }
      button.icon.stock.out-of-stock { color: var(--secondary-text-color); }
      tr.dimmed { opacity: 0.5; }
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

      /* P8b rich-field block */
      .fill-llm-button {
        align-self: flex-start;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      .fill-llm-button:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .fill-llm-button:hover:not(:disabled) { opacity: 0.9; }
      .autofill-error {
        background: var(--warning-color, #ff9800);
        color: var(--text-primary-color);
        border-radius: 4px;
        padding: 6px 10px;
        font-size: 12px;
      }

      fieldset.rich-group {
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        padding: 8px 12px;
      }
      fieldset.rich-group legend {
        padding: 0 4px;
        font-size: 12px;
        color: var(--secondary-text-color);
      }

      .chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        padding: 4px 0;
      }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 12px;
        border: 1px solid var(--divider-color);
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 12px;
        cursor: pointer;
        font-family: inherit;
      }
      .chip:hover { background: var(--secondary-background-color); }
      .chip.removable {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border-color: var(--primary-color);
      }
      .chip-x {
        font-size: 14px;
        line-height: 1;
        opacity: 0.8;
      }
      .chip.toggle.active {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border-color: var(--primary-color);
        font-weight: 500;
      }

      .chip-add {
        display: flex;
        gap: 6px;
        margin-top: 8px;
      }
      .chip-add input {
        flex: 1;
        padding: 6px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
      }
      .chip-add-btn {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
    `;
  }
}

if (!customElements.get('melitta-additives')) customElements.define('melitta-additives', MelittaAdditives);
