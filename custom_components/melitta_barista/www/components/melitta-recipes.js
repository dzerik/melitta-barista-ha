/**
 * Recipes / DirectKey viewer.
 *
 * Pulls the live recipe cache from the integration via
 * `melitta_barista/recipes/list` and renders two sections:
 *   - Base recipes (HR/HS) with their two components.
 *   - DirectKey profiles, each as a sub-table of category recipes.
 *
 * Editing flow lands in a follow-up commit; for now the read-only view is the
 * single most useful tab for understanding what the machine has stored.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

class MelittaRecipes extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _data: { type: Object },
      _error: { type: String },
      _loading: { type: Boolean },
    };
  }

  constructor() {
    super();
    this._data = null;
    this._error = "";
    this._loading = false;
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  updated(changedProps) {
    if (changedProps.has("entryId") && this.entryId) this._load();
  }

  async _load() {
    if (!this.hass || !this.entryId) return;
    this._loading = true;
    try {
      this._data = await this.hass.callWS({
        type: "melitta_barista/recipes/list",
        entry_id: this.entryId,
      });
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._loading = false;
    }
  }

  _renderComponent(comp) {
    if (!comp) return html`<span class="dim">—</span>`;
    return html`
      <div class="comp">
        <span class="proc">${comp.process}</span>
        ${comp.portion_ml ? html`<span class="ml">${comp.portion_ml} ml</span>` : ""}
        ${comp.intensity && comp.intensity !== "medium" ? html`<span class="badge">${comp.intensity}</span>` : ""}
        ${comp.aroma && comp.aroma !== "standard" ? html`<span class="badge">${comp.aroma}</span>` : ""}
        ${comp.temperature && comp.temperature !== "normal" ? html`<span class="badge">${comp.temperature}</span>` : ""}
        ${comp.shots && comp.shots !== "none" ? html`<span class="badge">${comp.shots}</span>` : ""}
      </div>
    `;
  }

  _renderRecipeRow(r) {
    return html`
      <tr>
        <td class="id">${r.id}</td>
        <td>${r.name || html`<span class="dim">—</span>`}</td>
        <td>${this._renderComponent(r.components?.[0])}</td>
        <td>${this._renderComponent(r.components?.[1])}</td>
      </tr>
    `;
  }

  _renderBaseRecipes(rows) {
    if (!rows || rows.length === 0) {
      return html`<div class="hint">${this._t("common.empty")}</div>`;
    }
    return html`
      <table class="recipes">
        <thead>
          <tr>
            <th class="id">${this._t("recipes.id")}</th>
            <th>${this._t("recipes.name")}</th>
            <th>1</th>
            <th>2</th>
          </tr>
        </thead>
        <tbody>${rows.map((r) => this._renderRecipeRow(r))}</tbody>
      </table>
    `;
  }

  _renderDirectKey(profiles) {
    if (!profiles || profiles.length === 0) {
      return html`<div class="hint">${this._t("common.empty")}</div>`;
    }
    return html`
      <div class="profiles">
        ${profiles.map((p) => html`
          <div class="profile">
            <h4>${p.profile_name} <span class="dim">#${p.profile_id}</span></h4>
            <table class="recipes">
              <thead>
                <tr>
                  <th class="id">${this._t("recipes.id")}</th>
                  <th>${this._t("recipes.category")}</th>
                  <th>1</th>
                  <th>2</th>
                </tr>
              </thead>
              <tbody>
                ${p.recipes.map((r) => this._renderRecipeRow(r))}
              </tbody>
            </table>
          </div>
        `)}
      </div>
    `;
  }

  render() {
    if (this._error) {
      return html`<div class="error">${this._t("common.error")}: ${this._error}</div>`;
    }
    if (!this._data && this._loading) {
      return html`<div class="hint">${this._t("common.loading")}</div>`;
    }

    return html`
      <section class="card">
        <header class="card-head">
          <h2>${this._t("recipes.title")}</h2>
          <button class="action" @click=${() => this._load()}>${this._t("common.refresh")}</button>
        </header>

        <h3>${this._t("recipes.base_recipes")}</h3>
        ${this._renderBaseRecipes(this._data?.base_recipes)}

        <h3>${this._t("recipes.directkey")}</h3>
        ${this._renderDirectKey(this._data?.directkey)}

        <p class="hint small">${this._t("recipes.coming_soon")}</p>
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
      .card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      }
      h2 { margin: 0; font-size: 18px; }
      h3 {
        margin: 20px 0 8px;
        font-size: 14px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      h4 { margin: 12px 0 6px; font-size: 14px; }
      table.recipes {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
      }
      table.recipes th {
        text-align: left;
        padding: 6px 8px;
        color: var(--secondary-text-color);
        font-weight: 500;
        border-bottom: 1px solid var(--divider-color);
      }
      table.recipes td {
        padding: 6px 8px;
        border-bottom: 1px solid var(--divider-color);
        vertical-align: top;
      }
      .id { width: 48px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .dim { color: var(--secondary-text-color); }
      .comp {
        display: flex;
        flex-wrap: wrap;
        gap: 4px 8px;
        align-items: center;
      }
      .proc { font-weight: 500; }
      .ml { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .badge {
        font-size: 11px;
        background: var(--secondary-background-color);
        padding: 2px 6px;
        border-radius: 3px;
        color: var(--secondary-text-color);
      }
      .profiles { display: flex; flex-direction: column; gap: 8px; }
      .profile {
        border: 1px solid var(--divider-color);
        border-radius: 6px;
        padding: 8px 12px;
        background: var(--secondary-background-color);
      }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      .hint.small { font-size: 12px; margin-top: 16px; }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }
      button.action {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.action:hover { opacity: 0.9; }
    `;
  }
}

if (!customElements.get('melitta-recipes')) customElements.define('melitta-recipes', MelittaRecipes);
