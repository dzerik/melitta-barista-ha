/**
 * Add-ins manager: syrups, toppings, milk types.
 *
 * One unified add/edit modal with a type picker (syrup / topping / milk).
 * Inline tables show the existing entries with edit + delete controls.
 * Milk types use the existing sommelier-side single-list endpoint and only
 * carry a name; syrups and toppings have their own normalised tables and
 * expose the P8a-introduced rich fields (producer / variant /
 * flavor_notes / composition / attributes).
 *
 * Modal field order (P12-B — mirrors the beans modal):
 *   producer dropdown → name → Fill-from-LLM → variant → notes →
 *   composition → flavor_notes chips → attributes chips.
 * The standalone Brand text input is gone — the producer dropdown is the
 * single source of brand identity. `fields.brand` is still populated on
 * save (from the selected producer's name) so the existing `brand`
 * column / external consumers keep working without a DB migration.
 *
 * Syrups & toppings additionally support a "Fill from LLM" button that
 * routes to the P8b backend autofill endpoint and merges the parsed
 * response into the editing state. The autofill WS payload now carries
 * `{name, producer_id, variant?, website?}` — the backend resolves the
 * producer name (and a website fallback) from the producers table.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n/index.js";
import { freeFormLabel, labelFor } from "../i18n/server-strings.js";
import "./melitta-confirm.js";

/**
 * extras_kind tokens with a backing storage table in this panel — the
 * whitelist that gates the served `vocab.extras_kind` list (UI Contract
 * §9.0.3: an unknown served token is ignored, never invented a table
 * for) and the fallback fixture when the vocab is absent (§9.2.6.1).
 * `milk` is deliberately NOT here: milk types are a free-form family
 * (§9.2.4) stored in their own panel-local list, so the kind picker
 * appends it outside the vocabulary.
 */
const EXTRAS_KIND_TABLES = ["syrup", "topping"];
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
      vocab: { attribute: false },
      serverStrings: { attribute: false },
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
    this.vocab = null;
    this.serverStrings = null;
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
   * Kind tokens for the type picker: served `vocab.extras_kind.tokens`
   * filtered to the kinds this panel has storage tables for (§9.0.3 —
   * unknown served tokens are ignored), falling back to the whitelist
   * itself (§9.2.6.1); `milk` is appended outside the vocabulary (a
   * free-form family, §9.2.4).
   */
  _kindTokens() {
    const served = this.vocab?.extras_kind?.tokens;
    let kinds = Array.isArray(served) && served.length
      ? served.filter((k) => EXTRAS_KIND_TABLES.includes(k))
      : EXTRAS_KIND_TABLES;
    if (!kinds.length) kinds = EXTRAS_KIND_TABLES;
    return kinds.concat("milk");
  }

  /**
   * Kind label (§9.2.6.2 chain): server string
   * `sommelier.extras_kind.<token>` → panel bundle (`modal.type.<token>`,
   * which also covers the vocab-external `milk`) → humanized token.
   */
  _kindLabel(token) {
    const key = `modal.type.${token}`;
    const bundled = this._t(key);
    return labelFor(
      `sommelier.extras_kind.${token}`,
      bundled === key ? null : bundled,
      token,
    );
  }

  /**
   * Item label for one of the free-form suggestion families (§6.3.7):
   * served `sommelier.<family>.<value>` when the stored value happens to
   * be a well-known token (`oat`, `vanilla`, `chocolate`, …), otherwise
   * the user's own product name verbatim.
   *
   * These tables are open by design (§9.2.4): the served labels are
   * display sugar and never gate what may be stored, so every id, write
   * and delete keeps using the raw value.
   */
  _itemLabel(family, value) {
    return freeFormLabel(family, value);
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
        this.hass.callWS({ type: "melitta_barista/sommelier/milk/list_full" }),
        this.hass.callWS({ type: "melitta_barista/producers/list" }).catch(() => ({ producers: [] })),
      ]);
      this._syrups = s.syrups || [];
      this._toppings = tp.toppings || [];
      // milk rows carry an `available` flag now (P12-C). The id is the
      // milk_type string itself — it's the natural primary key.
      this._milk = (m.milks || []).map((row) => ({
        id: row.milk_type,
        name: row.milk_type,
        available: row.available !== false,
      }));
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
    if (!e || e.type === "milk") return;
    if (!e.name || !e.name.trim() || e.producer_id == null) {
      this._autofillError = this._t("additives.fill_needs_producer_and_name");
      return;
    }
    const table = e.type === "syrup" ? "syrups" : "toppings";
    // Resolve producer.website (when present) so the LLM gets an extra
    // hint. The dropdown is bound to producer.id; we look the row up by
    // matching numeric id with String() to handle the FE's stringified
    // <select> value too.
    const producer = this._producers.find(
      (p) => String(p.id) === String(e.producer_id),
    );
    const website = (producer && producer.website || "").trim();
    this._autofillBusy = true;
    this._autofillError = "";
    try {
      const payload = {
        type: `melitta_barista/${table}/autofill`,
        name: e.name.trim(),
        producer_id: Number(e.producer_id),
      };
      if (e.variant && e.variant.trim()) {
        payload.variant = e.variant.trim();
      }
      if (website) {
        payload.website = website;
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
        // Resolve the selected producer once so we can mirror its name
        // into the legacy `brand` column (keeps existing queries / external
        // consumers working without a DB migration).
        const producer =
          e.producer_id != null && e.producer_id !== ""
            ? this._producers.find(
                (p) => String(p.id) === String(e.producer_id),
              )
            : null;
        // Coerce DB NULLs to "" — voluptuous Optional(...): str rejects None.
        const fields = {
          name: e.name,
          brand: producer ? (producer.name || "") : "",
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
    const next = !(item.available ?? 1);
    try {
      if (type === "milk") {
        await this.hass.callWS({
          type: "melitta_barista/sommelier/milk/set_available",
          milk_type: item.name,
          available: next,
        });
      } else {
        const table = type === "syrup" ? "syrups" : "toppings";
        await this.hass.callWS({
          type: `melitta_barista/${table}/set_available`,
          additive_id: item.id,
          available: next,
        });
      }
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
            const rowClass = !inStock ? "dimmed" : "";
            return html`
            <tr class=${rowClass}>
              <td title=${r.name}>${this._itemLabel(type, r.name)}</td>
              ${showsBrand ? html`<td>${r.brand || ""}</td>` : ""}
              ${showsBrand ? html`<td>${r.notes || ""}</td>` : ""}
              <td class="actions">
                <button
                  class="icon stock ${inStock ? "in-stock" : "out-of-stock"}"
                  title=${inStock ? this._t("additives.in_stock") : this._t("additives.out_of_stock")}
                  @click=${() => this._toggleAvailable(type, r)}
                >${inStock ? "✓" : "○"}</button>
                ${type !== "milk"
                  ? html`<button class="icon edit" @click=${() => this._openEdit(type, r)}>✎</button>`
                  : ""}
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
      <label>${this._t("additives.variant")}
        <input type="text" .value=${e.variant || ""}
          @input=${(ev) => this._updateField("variant", ev.target.value)} />
      </label>

      <label>${this._t("additives.notes")}
        <textarea rows="3"
          @input=${(ev) => this._updateField("notes", ev.target.value)}
        >${e.notes || ""}</textarea>
      </label>

      <label>${this._t("additives.composition")}
        <textarea rows="3"
          .value=${e.composition || ""}
          @input=${(ev) => this._updateField("composition", ev.target.value)}
        >${e.composition || ""}</textarea>
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
            >${this._itemLabel("note", n)} <span class="chip-x">×</span></button>
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
    const isRich = e.type !== "milk";
    const canAutofill =
      isRich
      && !!e.name && !!e.name.trim()
      && e.producer_id != null && e.producer_id !== "";
    return html`
      <melitta-modal .open=${true} .title=${this._t(titleKey)}
        @close=${() => this._closeModal()}>
        <div class="form">
          <label>${this._t("modal.type")}
            <select .value=${e.type} ?disabled=${!!e.id}
              @change=${(ev) => this._updateField("type", ev.target.value)}>
              ${this._kindTokens().map((tp) => html`
                <option value=${tp} ?selected=${tp === e.type}>
                  ${this._kindLabel(tp)}
                </option>
              `)}
            </select>
          </label>
          ${isRich ? html`
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
            <label>${this._t("additives.name")}
              <input type="text" .value=${e.name}
                @input=${(ev) => this._updateField("name", ev.target.value)} /></label>
            <button
              class="fill-llm-button"
              ?disabled=${this._autofillBusy || !canAutofill}
              @click=${() => this._runAutofill()}
              title=${!canAutofill ? this._t("additives.fill_needs_producer_and_name") : ""}
            >
              ${this._autofillBusy ? "…" : "✨"} ${this._t("additives.fill_from_llm")}
            </button>
            ${this._autofillError ? html`
              <div class="autofill-error">${this._autofillError}</div>
            ` : ""}
            ${this._renderRichFields(e)}
          ` : html`
            <label>${this._t("additives.name")}
              <input type="text" .value=${e.name}
                @input=${(ev) => this._updateField("name", ev.target.value)} /></label>
          `}
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
