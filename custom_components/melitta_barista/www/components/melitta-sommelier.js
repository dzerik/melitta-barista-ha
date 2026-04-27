/**
 * AI Sommelier — chat-style recipe generation + one-click brew.
 *
 * Pipeline:
 *   1. User picks mode + optional preference, hits Generate.
 *   2. WS `melitta_barista/sommelier/generate` returns a session with N
 *      structured recipes, each carrying a recipe_id.
 *   3. UI shows the recipes; "Brew this" calls
 *      `melitta_barista/sommelier/brew` with the recipe_id, which the
 *      backend converts to a freestyle HE payload and sends to the machine
 *      via brew_freestyle (the end-to-end mapping that previously didn't
 *      have a UI entry point).
 *
 * Keeps a per-session log of the last few brews for context.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const MODES = [
  { id: "surprise_me", label_en: "Surprise me", label_ru: "Удиви меня" },
  { id: "custom", label_en: "Custom request", label_ru: "Свой запрос" },
];

class MelittaSommelier extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _mode: { type: String },
      _preference: { type: String },
      _count: { type: Number },
      _generating: { type: Boolean },
      _session: { type: Object },
      _brewing: { type: String }, // recipe_id currently brewing
      _info: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._mode = "surprise_me";
    this._preference = "";
    this._count = 3;
    this._generating = false;
    this._session = null;
    this._brewing = "";
    this._info = "";
    this._error = "";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  _modeLabel(m) {
    return (this.lang || "").startsWith("ru") ? m.label_ru : m.label_en;
  }

  async _generate() {
    if (!this.hass) return;
    this._generating = true;
    this._error = "";
    this._info = "";
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/sommelier/generate",
        mode: this._mode,
        preference: this._preference || undefined,
        count: this._count,
      });
      this._session = result.session;
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._generating = false;
    }
  }

  async _brew(recipeId) {
    this._brewing = recipeId;
    this._info = this._t("sommelier.brewing");
    this._error = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/brew",
        recipe_id: recipeId,
      });
      this._info = this._t("sommelier.brew_ok");
    } catch (e) {
      this._error = `${this._t("sommelier.brew_failed")}: ${e.message || e}`;
      this._info = "";
    } finally {
      this._brewing = "";
    }
  }

  _renderForm() {
    return html`
      <div class="form">
        <select .value=${this._mode}
          @change=${(e) => { this._mode = e.target.value; }}>
          ${MODES.map((m) => html`<option value=${m.id}>${this._modeLabel(m)}</option>`)}
        </select>
        <input type="number" min="1" max="5" .value=${this._count}
          @input=${(e) => { this._count = parseInt(e.target.value, 10) || 1; }} />
        ${this._mode === "custom" ? html`
          <input type="text" class="wide"
            .value=${this._preference}
            placeholder=${this._t("sommelier.prompt")}
            @input=${(e) => { this._preference = e.target.value; }}
            @keydown=${(e) => e.key === "Enter" && this._generate()} />
        ` : ""}
        <button class="generate"
          ?disabled=${this._generating}
          @click=${() => this._generate()}>
          ${this._generating ? this._t("common.loading") : this._t("sommelier.generate")}
        </button>
      </div>
    `;
  }

  _renderComponent(comp) {
    if (!comp || comp.process === "none" || comp.process === 0) return "";
    return html`
      <div class="comp">
        <span class="proc">${comp.process}</span>
        <span class="ml">${comp.portion_ml} ml</span>
        ${comp.intensity && comp.intensity !== "medium" ? html`<span class="badge">${comp.intensity}</span>` : ""}
        ${comp.aroma && comp.aroma !== "standard" ? html`<span class="badge">${comp.aroma}</span>` : ""}
        ${comp.temperature && comp.temperature !== "normal" ? html`<span class="badge">${comp.temperature}</span>` : ""}
      </div>
    `;
  }

  _renderRecipes() {
    if (!this._session || !Array.isArray(this._session.recipes)) {
      return html`<div class="hint">${this._t("sommelier.no_recipe")}</div>`;
    }
    return html`
      <div class="recipes">
        ${this._session.recipes.map((r) => html`
          <article class="recipe">
            <header>
              <h3>${r.name || "Recipe"}</h3>
              <button class="brew"
                ?disabled=${this._brewing === r.id}
                @click=${() => this._brew(r.id)}>
                ${this._brewing === r.id
                  ? this._t("sommelier.brewing")
                  : this._t("sommelier.brew_this")}
              </button>
            </header>
            ${r.description ? html`<p class="desc">${r.description}</p>` : ""}
            ${this._renderComponent(r.component1)}
            ${this._renderComponent(r.component2)}
            ${r.add_ins && Object.keys(r.add_ins || {}).length
              ? html`<div class="addins">
                  ${Object.entries(r.add_ins).map(([k, v]) =>
                    Array.isArray(v) && v.length
                      ? html`<span class="badge add">${k}: ${v.join(", ")}</span>`
                      : "")}
                </div>`
              : ""}
            ${r.reasoning ? html`
              <details class="reasoning">
                <summary>Why?</summary>
                <p>${r.reasoning}</p>
              </details>
            ` : ""}
          </article>
        `)}
      </div>
    `;
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("sommelier.title")}</h2>
        ${this._renderForm()}
        ${this._error ? html`<div class="error">${this._error}</div>` : ""}
        ${this._info ? html`<div class="info">${this._info}</div>` : ""}
        ${this._renderRecipes()}
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
      .form {
        display: grid;
        grid-template-columns: 1fr 80px auto;
        gap: 8px;
        margin-bottom: 16px;
      }
      .form select, .form input {
        padding: 8px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 14px;
      }
      .form input.wide { grid-column: 1 / -1; }
      button.generate {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 14px;
        font-weight: 500;
      }
      button.generate:hover:not(:disabled) { opacity: 0.9; }
      button.generate:disabled { opacity: 0.5; cursor: not-allowed; }

      .recipes {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 12px;
      }
      .recipe {
        background: var(--secondary-background-color);
        border-radius: 6px;
        padding: 12px 14px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .recipe header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .recipe h3 { margin: 0; font-size: 15px; }
      .desc { margin: 0; color: var(--secondary-text-color); font-size: 13px; }
      .comp { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 13px; }
      .proc { font-weight: 500; }
      .ml { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .badge {
        font-size: 11px;
        background: var(--primary-background-color);
        padding: 2px 6px;
        border-radius: 3px;
        color: var(--secondary-text-color);
      }
      .badge.add { background: var(--info-color, #2196f3); color: var(--text-primary-color); }
      .addins { display: flex; flex-wrap: wrap; gap: 4px; }
      .reasoning summary { font-size: 12px; cursor: pointer; color: var(--secondary-text-color); }
      .reasoning p { font-size: 12px; margin: 4px 0 0; color: var(--secondary-text-color); }

      button.brew {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
      }
      button.brew:hover:not(:disabled) { opacity: 0.9; }
      button.brew:disabled { opacity: 0.5; cursor: not-allowed; }

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
    `;
  }
}

customElements.define("melitta-sommelier", MelittaSommelier);
