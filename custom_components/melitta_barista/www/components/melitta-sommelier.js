/**
 * AI Sommelier — chat-style recipe generation + one-click brew.
 *
 * The form lets the user constrain what the LLM is allowed to suggest:
 *   - mode (Surprise me / Custom request) + optional free-text preference
 *   - cup size dropdown
 *   - moods (multi-select chips)
 *   - occasion (auto-suggested from local time on first mount)
 *   - temperature (auto / hot / iced)
 *   - caffeine preference
 *   - dietary restrictions (multi-select chips)
 *   - allow_syrups / allow_toppings / allow_milk (multi-select from the
 *     items the user has configured in the Additives tab; absent ⇒ all)
 *
 * Each generated recipe carries a heart button that ships the recipe id
 * to /sommelier/favorites/add.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const MODES = [
  { id: "surprise_me", label_en: "Surprise me", label_ru: "Удиви меня" },
  { id: "custom", label_en: "Custom request", label_ru: "Свой запрос" },
];

const CUP_SIZES = ["espresso_cup", "cup", "mug", "tall_glass", "travel"];
const MOODS = ["energizing", "relaxing", "dessert", "classic"];
const OCCASIONS = ["morning", "after_lunch", "guests", "romantic", "work"];
const TEMPERATURES = ["auto", "hot", "iced"];
const CAFFEINE_PREFS = ["regular", "low", "decaf_evening"];
const DIETARY = ["no_sugar", "lactose_free", "low_calorie", "vegan"];

class MelittaSommelier extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _mode: { type: String },
      _preference: { type: String },
      _count: { type: Number },
      _cupSize: { type: String },
      _moods: { type: Array },
      _occasion: { type: String },
      _temperature: { type: String },
      _caffeine: { type: String },
      _dietary: { type: Array },
      _availableSyrups: { type: Array },
      _availableToppings: { type: Array },
      _availableMilk: { type: Array },
      _allowSyrups: { type: Array },
      _allowToppings: { type: Array },
      _allowMilk: { type: Array },
      _showConstraints: { type: Boolean },
      _showAddins: { type: Boolean },
      _generating: { type: Boolean },
      _session: { type: Object },
      _brewing: { type: String },
      _favoriting: { type: String },
      _favoritedIds: { type: Array },
      _info: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._mode = "surprise_me";
    this._preference = "";
    this._count = 3;
    this._cupSize = "mug";
    this._moods = [];
    this._occasion = this._suggestOccasionByTime();
    this._temperature = "auto";
    this._caffeine = "regular";
    this._dietary = [];
    this._availableSyrups = [];
    this._availableToppings = [];
    this._availableMilk = [];
    this._allowSyrups = [];
    this._allowToppings = [];
    this._allowMilk = [];
    this._showConstraints = true;
    this._showAddins = true;
    this._generating = false;
    this._session = null;
    this._brewing = "";
    this._favoriting = "";
    this._favoritedIds = [];
    this._info = "";
    this._error = "";
  }

  /**
   * Pick a sensible default for "occasion" from the user's local clock.
   * 5–11 → morning, 12–16 → after_lunch, 17–21 → work (winding down),
   * 22–4 → guests (something special). Tunable later.
   */
  _suggestOccasionByTime() {
    const h = new Date().getHours();
    if (h >= 5 && h < 12) return "morning";
    if (h >= 12 && h < 17) return "after_lunch";
    if (h >= 17 && h < 22) return "work";
    return "guests";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  _modeLabel(m) {
    return (this.lang || "").startsWith("ru") ? m.label_ru : m.label_en;
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadAvailable();
  }

  async _loadAvailable() {
    if (!this.hass) return;
    try {
      const [syrups, toppings, milk] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/syrups/list" }),
        this.hass.callWS({ type: "melitta_barista/toppings/list" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/milk/get" }),
      ]);
      this._availableSyrups = (syrups.syrups || []).map((s) => s.name);
      this._availableToppings = (toppings.toppings || []).map((t) => t.name);
      this._availableMilk = milk.milk_types || [];
      // Default: select everything available so the user has to opt OUT
      // of an ingredient they don't want, not opt IN to each one.
      if (this._allowSyrups.length === 0) this._allowSyrups = [...this._availableSyrups];
      if (this._allowToppings.length === 0) this._allowToppings = [...this._availableToppings];
      if (this._allowMilk.length === 0) this._allowMilk = [...this._availableMilk];
    } catch (e) {
      this._error = `Не удалось загрузить добавки: ${e.message || e}`;
    }
  }

  _toggle(field, value) {
    const set = new Set(this[field] || []);
    set.has(value) ? set.delete(value) : set.add(value);
    this[field] = [...set];
  }

  async _generate() {
    if (!this.hass) return;
    this._generating = true;
    this._error = "";
    this._info = "";
    try {
      const payload = {
        type: "melitta_barista/sommelier/generate",
        mode: this._mode,
        count: this._count,
        cup_size: this._cupSize,
        temperature: this._temperature,
        caffeine_pref: this._caffeine,
        // Multi-select fields are sent ONLY when the user actually
        // narrowed the available list — sending the full universe is
        // equivalent to no filter and just wastes prompt tokens.
        moods: this._moods,
        dietary: this._dietary,
        allow_syrups: this._allowSyrups,
        allow_toppings: this._allowToppings,
        allow_milk: this._allowMilk,
      };
      if (this._preference) payload.preference = this._preference;
      if (this._occasion) payload.occasion = this._occasion;

      const result = await this.hass.callWS(payload);
      this._session = result.session;
      this._favoritedIds = [];
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

  async _favorite(recipeId) {
    if (this._favoritedIds.includes(recipeId)) return;
    this._favoriting = recipeId;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/favorites/add",
        recipe_id: recipeId,
      });
      this._favoritedIds = [...this._favoritedIds, recipeId];
      this._info = "★ В избранном";
    } catch (e) {
      this._error = `Не удалось добавить в избранное: ${e.message || e}`;
    } finally {
      this._favoriting = "";
    }
  }

  // ── form sections ──

  _renderHeader() {
    return html`
      <div class="form">
        <div class="row3">
          <select .value=${this._mode}
            @change=${(e) => { this._mode = e.target.value; }}>
            ${MODES.map((m) => html`
              <option value=${m.id} ?selected=${m.id === this._mode}>
                ${this._modeLabel(m)}
              </option>
            `)}
          </select>
          <input type="number" min="1" max="5" .value=${this._count}
            @input=${(e) => { this._count = parseInt(e.target.value, 10) || 1; }} />
          <button class="generate"
            ?disabled=${this._generating}
            @click=${() => this._generate()}>
            ${this._generating ? this._t("common.loading") : this._t("sommelier.generate")}
          </button>
        </div>
        ${this._mode === "custom" ? html`
          <input type="text" class="wide"
            .value=${this._preference}
            placeholder=${this._t("sommelier.prompt")}
            @input=${(e) => { this._preference = e.target.value; }}
            @keydown=${(e) => e.key === "Enter" && this._generate()} />
        ` : ""}
      </div>
    `;
  }

  _renderConstraints() {
    return html`
      <details class="block" ?open=${this._showConstraints}
        @toggle=${(e) => { this._showConstraints = e.target.open; }}>
        <summary>Ограничения и настроение</summary>
        <div class="block-body">
          <div class="field">
            <label>Объём чашки</label>
            <select .value=${this._cupSize}
              @change=${(e) => { this._cupSize = e.target.value; }}>
              ${CUP_SIZES.map((c) => html`
                <option value=${c} ?selected=${c === this._cupSize}>${c}</option>
              `)}
            </select>
          </div>

          <div class="field">
            <label>Настроение (можно несколько)</label>
            <div class="chips">
              ${MOODS.map((m) => html`
                <button class=${this._moods.includes(m) ? "chip on" : "chip"}
                  @click=${() => this._toggle("_moods", m)}>${m}</button>
              `)}
            </div>
          </div>

          <div class="field">
            <label>Повод (предложен по времени суток)</label>
            <select .value=${this._occasion}
              @change=${(e) => { this._occasion = e.target.value; }}>
              <option value="" ?selected=${!this._occasion}>—</option>
              ${OCCASIONS.map((o) => html`
                <option value=${o} ?selected=${o === this._occasion}>${o}</option>
              `)}
            </select>
          </div>

          <div class="field">
            <label>Температура</label>
            <div class="chips">
              ${TEMPERATURES.map((t_) => html`
                <button class=${this._temperature === t_ ? "chip on" : "chip"}
                  @click=${() => { this._temperature = t_; }}>${t_}</button>
              `)}
            </div>
          </div>

          <div class="field">
            <label>Кофеин</label>
            <select .value=${this._caffeine}
              @change=${(e) => { this._caffeine = e.target.value; }}>
              ${CAFFEINE_PREFS.map((c) => html`
                <option value=${c} ?selected=${c === this._caffeine}>${c}</option>
              `)}
            </select>
          </div>

          <div class="field">
            <label>Диетические ограничения (можно несколько)</label>
            <div class="chips">
              ${DIETARY.map((d) => html`
                <button class=${this._dietary.includes(d) ? "chip on" : "chip"}
                  @click=${() => this._toggle("_dietary", d)}>${d}</button>
              `)}
            </div>
          </div>
        </div>
      </details>
    `;
  }

  _renderAddinSection(title, available, selectedField) {
    if (available.length === 0) {
      return html`
        <div class="field">
          <label>${title}</label>
          <span class="hint">— не настроено в Добавках</span>
        </div>
      `;
    }
    const selected = this[selectedField] || [];
    return html`
      <div class="field">
        <label>${title}</label>
        <div class="chips">
          ${available.map((item) => html`
            <button class=${selected.includes(item) ? "chip on" : "chip"}
              @click=${() => this._toggle(selectedField, item)}>${item}</button>
          `)}
        </div>
      </div>
    `;
  }

  _renderAddins() {
    return html`
      <details class="block" ?open=${this._showAddins}
        @toggle=${(e) => { this._showAddins = e.target.open; }}>
        <summary>Доступные добавки (мульти-выбор)</summary>
        <div class="block-body">
          ${this._renderAddinSection("Сиропы", this._availableSyrups, "_allowSyrups")}
          ${this._renderAddinSection("Топинги", this._availableToppings, "_allowToppings")}
          ${this._renderAddinSection("Молоко", this._availableMilk, "_allowMilk")}
        </div>
      </details>
    `;
  }

  // ── recipe rendering ──

  _renderComponent(comp) {
    if (!comp || comp.process === "none" || comp.process === 0) return "";
    return html`
      <div class="comp">
        <span class="proc">${comp.process}</span>
        <span class="ml">${comp.portion_ml} ml</span>
        ${comp.intensity && comp.intensity !== "medium"
          ? html`<span class="badge">${comp.intensity}</span>` : ""}
        ${comp.aroma && comp.aroma !== "standard"
          ? html`<span class="badge">${comp.aroma}</span>` : ""}
        ${comp.temperature && comp.temperature !== "normal"
          ? html`<span class="badge">${comp.temperature}</span>` : ""}
      </div>
    `;
  }

  _renderSteps(steps) {
    if (!Array.isArray(steps) || steps.length === 0) return "";
    const sorted = steps
      .filter((s) => s && typeof s === "object")
      .slice()
      .sort((a, b) => (a.order || 0) - (b.order || 0));
    return html`
      <ol class="steps">
        ${sorted.map((s) => html`
          <li>
            <span class="action">${s.action || ""}</span>
            ${s.ingredient
              ? html`<span class="ingredient">— ${s.ingredient}</span>` : ""}
            ${s.amount != null
              ? html`<span class="dose">${s.amount} ${s.unit || ""}</span>` : ""}
            ${s.notes ? html`<div class="step-notes">${s.notes}</div>` : ""}
          </li>
        `)}
      </ol>
    `;
  }

  _renderExtrasSummary(extras) {
    if (!extras || typeof extras !== "object") return "";
    const chips = [];
    if (extras.ice) chips.push("ice");
    if (extras.syrup) chips.push(`syrup: ${extras.syrup}`);
    if (extras.topping) chips.push(`topping: ${extras.topping}`);
    if (extras.liqueur) chips.push(`liqueur: ${extras.liqueur}`);
    if (chips.length === 0 && !extras.instruction) return "";
    return html`
      <div class="addins">
        ${chips.map((c) => html`<span class="badge add">${c}</span>`)}
        ${extras.instruction
          ? html`<div class="extra-instruction">${extras.instruction}</div>`
          : ""}
      </div>
    `;
  }

  _renderRecipes() {
    if (!this._session || !Array.isArray(this._session.recipes)) {
      return html`<div class="hint">${this._t("sommelier.no_recipe")}</div>`;
    }
    return html`
      <div class="recipes">
        ${this._session.recipes.map((r) => {
          const fav = this._favoritedIds.includes(r.id);
          return html`
          <article class="recipe">
            <header>
              <h3>${r.name || "Recipe"}</h3>
              <div class="actions">
                <button class="fav"
                  ?disabled=${this._favoriting === r.id || fav}
                  title=${fav ? "В избранном" : "Добавить в избранное"}
                  @click=${() => this._favorite(r.id)}>
                  ${fav ? "★" : "☆"}
                </button>
                <button class="brew"
                  ?disabled=${this._brewing === r.id}
                  @click=${() => this._brew(r.id)}>
                  ${this._brewing === r.id
                    ? this._t("sommelier.brewing")
                    : this._t("sommelier.brew_this")}
                </button>
              </div>
            </header>
            ${r.description ? html`<p class="desc">${r.description}</p>` : ""}

            <div class="machine-line">
              <span class="machine-label">Machine:</span>
              ${this._renderComponent(r.component1)}
              ${this._renderComponent(r.component2)}
            </div>

            ${this._renderSteps(r.steps)}
            ${this._renderExtrasSummary(r.extras)}

            ${r.cup_type || r.estimated_caffeine || r.calories_approx ? html`
              <div class="meta">
                ${r.cup_type ? html`<span class="badge">${r.cup_type}</span>` : ""}
                ${r.estimated_caffeine
                  ? html`<span class="badge">caffeine: ${r.estimated_caffeine}</span>` : ""}
                ${r.calories_approx
                  ? html`<span class="badge">~${r.calories_approx} kcal</span>` : ""}
              </div>
            ` : ""}

            ${r.reasoning ? html`
              <details class="reasoning">
                <summary>Why?</summary>
                <p>${r.reasoning}</p>
              </details>
            ` : ""}
          </article>`;
        })}
      </div>
    `;
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("sommelier.title")}</h2>
        ${this._renderHeader()}
        ${this._renderConstraints()}
        ${this._renderAddins()}
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
      .form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
      .row3 {
        display: grid;
        grid-template-columns: 1fr 80px auto;
        gap: 8px;
      }
      .form select, .form input {
        padding: 8px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 14px;
      }
      .form input.wide { width: 100%; }
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

      details.block {
        margin: 8px 0;
        background: var(--secondary-background-color);
        border-radius: 6px;
        padding: 6px 12px;
      }
      details.block > summary {
        cursor: pointer;
        font-size: 13px;
        color: var(--secondary-text-color);
        padding: 4px 0;
      }
      details.block .block-body {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 8px 0;
      }
      .field { display: flex; flex-direction: column; gap: 4px; }
      .field > label {
        font-size: 11px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .field select {
        padding: 6px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
        max-width: 320px;
      }
      .chips { display: flex; flex-wrap: wrap; gap: 4px; }
      .chip {
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 12px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        border: 1px solid var(--divider-color);
        cursor: pointer;
      }
      .chip.on {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border-color: var(--primary-color);
      }

      .recipes {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 12px;
        margin-top: 12px;
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
      .actions { display: flex; gap: 4px; }
      .desc { margin: 0; color: var(--secondary-text-color); font-size: 13px; }
      .machine-line {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: baseline;
        font-size: 13px;
      }
      .machine-label {
        font-size: 11px;
        text-transform: uppercase;
        color: var(--secondary-text-color);
        letter-spacing: 0.5px;
      }
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
      .extra-instruction {
        font-size: 12px;
        color: var(--secondary-text-color);
        margin-top: 4px;
      }
      ol.steps {
        margin: 4px 0 0;
        padding-left: 24px;
        font-size: 13px;
        line-height: 1.5;
      }
      ol.steps li { margin: 2px 0; }
      ol.steps .action { font-weight: 500; }
      ol.steps .ingredient { color: var(--secondary-text-color); }
      ol.steps .dose {
        margin-left: 6px;
        background: var(--primary-background-color);
        padding: 1px 6px;
        border-radius: 3px;
        font-variant-numeric: tabular-nums;
      }
      ol.steps .step-notes {
        font-size: 12px;
        color: var(--secondary-text-color);
        margin-top: 2px;
      }
      .meta { display: flex; flex-wrap: wrap; gap: 4px; }
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
      button.fav {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--warning-color, #ff9800);
        padding: 4px 10px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 16px;
        line-height: 1;
      }
      button.fav:hover:not(:disabled) {
        background: var(--secondary-background-color);
      }
      button.fav:disabled { opacity: 0.7; cursor: default; }

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
      .hint { color: var(--secondary-text-color); padding: 8px 0; font-size: 13px; }
    `;
  }
}

customElements.define("melitta-sommelier", MelittaSommelier);
