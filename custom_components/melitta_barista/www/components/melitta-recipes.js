/**
 * DirectKey profile recipe editor.
 *
 * Pulls the live recipe caches via `melitta_barista/recipes/list` and lets
 * the user edit the machine's per-profile DirectKey recipes:
 *   - profile selector (real profile names, "Profile N" fallback),
 *   - grid of the 7 DirectKey categories as cards (name + drink icon),
 *   - modal edit form for component 1 / component 2 (process, intensity,
 *     aroma, temperature, shots, portion), Save via the existing
 *     `melitta_barista.save_directkey` HA service,
 *   - per-recipe reset-to-default via `melitta_barista.reset_recipe`.
 *
 * Form option lists and portion clamps come from the UI Contract document
 * (`.contract` property fed by the panel shell's single contract fetch)
 * with the §6.1.5 three-tier fallback per parameter: the v2 `parameters`
 * catalog → the v1 `vocabularies.freestyle` / `limits.portion_ml` blocks
 * → hardcoded fallbacks matching the service schema defaults. Option and
 * DirectKey category labels prefer server-served strings
 * (`values.<family>.<token>` / `values.directkey_category.<token>`,
 * §6.3.5) over the panel bundle. Since 0.93 (UI Contract v3): row
 * category identity is the served `category` token (§9.3.4 — no BLE
 * slot-id math here), and empty-slot component defaults come from the
 * `save_directkey` action-catalog entry's introspected params (§9.3.5).
 * Editing is disabled gracefully while the machine is disconnected; the
 * base-recipe table stays available as a collapsed read-only reference.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n/index.js";
import { displayNameFor, labelFor } from "../i18n/server-strings.js";
import "./melitta-confirm.js";
import "./ui/melitta-drink-icon.js";

/**
 * Fallback DirectKey category tokens, index-aligned with the enum order.
 * Since 0.93 every `recipes/list` DirectKey row carries its own
 * `category` token (UI Contract §9.3.4) — this array is only the
 * positional tier-2 fixture for a row without one (§5.3.6). The BLE
 * slot-id math this file used to duplicate is gone: the server derives
 * the token.
 */
const DIRECTKEY_CATEGORIES = [
  "espresso", "cafe_creme", "cappuccino", "latte_macchiato",
  "milk_froth", "milk", "water",
];

/**
 * Fallback freestyle vocabularies matching the save_directkey service
 * schema — used when the UI Contract document is unavailable (older
 * backend, pre-handshake machine).
 */
const FALLBACK_FREESTYLE = {
  process: ["none", "coffee", "milk", "water"],
  intensity: ["very_mild", "mild", "medium", "strong", "very_strong"],
  aroma: ["standard", "intense"],
  temperature: ["cold", "normal", "high"],
  shots: ["none", "one", "two", "three"],
};

/** Fallback portion clamps matching the service schema defaults. */
const FALLBACK_PORTION_LIMITS = {
  c1: { min: 5, max: 250, step: 5 },
  c2: { min: 0, max: 250, step: 5 },
};

/**
 * Fallback component defaults for empty slots, mirroring the service
 * schema. Tier 2 only (§5.3.6): the live defaults come from the
 * `save_directkey` action-catalog entry's introspected params (§9.3.5)
 * via `_componentDefaults()`.
 */
const FALLBACK_DEFAULT_C1 = {
  process: "coffee", intensity: "medium", aroma: "standard",
  temperature: "normal", shots: "one", portion_ml: 40,
};
const FALLBACK_DEFAULT_C2 = {
  process: "none", intensity: "medium", aroma: "standard",
  temperature: "normal", shots: "none", portion_ml: 0,
};

class MelittaRecipes extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      contract: { attribute: false },
      serverStrings: { attribute: false },
      _data: { type: Object },
      _status: { type: Object },
      _profileId: { state: true },
      _editing: { state: true },
      _busy: { state: true },
      _error: { type: String },
      _loading: { type: Boolean },
    };
  }

  constructor() {
    super();
    this.contract = null;
    this.serverStrings = null;
    this._data = null;
    this._status = null;
    this._profileId = null;
    this._editing = null;
    this._busy = false;
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

  /** Fetch the recipe caches and a status snapshot (connection, profile). */
  async _load() {
    if (!this.hass || !this.entryId) return;
    this._loading = true;
    try {
      const [data, status] = await Promise.all([
        this.hass.callWS({
          type: "melitta_barista/recipes/list",
          entry_id: this.entryId,
        }),
        this.hass.callWS({
          type: "melitta_barista/status",
          entry_id: this.entryId,
        }),
      ]);
      this._data = data;
      this._status = status;
      this._error = "";
      this._pickDefaultProfile();
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._loading = false;
    }
  }

  _profiles() {
    return this._data?.directkey || [];
  }

  _profile() {
    return this._profiles().find((p) => p.profile_id === this._profileId) || null;
  }

  /** Keep the selection; else prefer the machine's active profile. */
  _pickDefaultProfile() {
    const profiles = this._profiles();
    if (profiles.some((p) => p.profile_id === this._profileId)) return;
    const active = this._status?.active_profile;
    const match = profiles.find((p) => p.profile_id === active);
    this._profileId = (match || profiles[0])?.profile_id ?? null;
  }

  _connected() {
    return Boolean(this._status?.connected);
  }

  // ── contract-driven vocabularies & limits ──────────────────────────

  /**
   * Enum descriptor from the v2 `parameters` catalog for a freestyle
   * family, or null when the catalog is absent / the descriptor has an
   * unknown kind or a scope without "freestyle" (UI Contract §6.1.5 —
   * such parameters fall back tier by tier, never error).
   */
  _enumParameter(key) {
    const desc = this.contract?.parameters?.[key];
    if (!desc || typeof desc !== "object" || desc.kind !== "enum") return null;
    if (Array.isArray(desc.scope) && !desc.scope.includes("freestyle")) {
      return null;
    }
    return Array.isArray(desc.tokens) && desc.tokens.length ? desc : null;
  }

  /**
   * Freestyle token list with the §6.1.5 three-tier fallback:
   * `parameters.<family>.tokens` → `vocabularies.freestyle.<family>` →
   * hardcoded service-schema defaults.
   */
  _vocab(key) {
    const desc = this._enumParameter(key);
    if (desc) return desc.tokens;
    const list = this.contract?.vocabularies?.freestyle?.[key];
    return Array.isArray(list) && list.length ? list : FALLBACK_FREESTYLE[key];
  }

  /**
   * Portion clamps ({min,max,step}) for "c1"/"c2" with the §6.1.5
   * three-tier fallback: `parameters.portion_ml.<comp>` →
   * `limits.portion_ml.<comp>` → service-schema defaults.
   */
  _portionLimits(comp) {
    const range = this.contract?.parameters?.portion_ml;
    if (
      range && typeof range === "object" && range.kind === "range"
      && range.per_component && range[comp] && typeof range[comp] === "object"
    ) {
      return range[comp];
    }
    const limits = this.contract?.limits?.portion_ml?.[comp];
    return limits && typeof limits === "object"
      ? limits
      : FALLBACK_PORTION_LIMITS[comp];
  }

  /** Clamp + step-snap a portion value against the contract limits. */
  _clampPortion(value, comp) {
    const { min, max, step } = { ...FALLBACK_PORTION_LIMITS[comp], ...this._portionLimits(comp) };
    let v = Number(value);
    if (!Number.isFinite(v)) v = min;
    v = Math.round(v / step) * step;
    return Math.min(max, Math.max(min, v));
  }

  /**
   * Introspected params of the `save_directkey` action-catalog entry
   * (UI Contract §9.3.5), or null when the contract/catalog/entry is
   * absent (pre-handshake, older backend) — graceful absence per §9.0.4.
   */
  _saveDirectkeyParams() {
    const actions = this.contract?.actions;
    if (!Array.isArray(actions)) return null;
    const entry = actions.find(
      (a) => a && typeof a === "object" && a.action === "save_directkey",
    );
    const params = entry?.invocation?.params;
    return Array.isArray(params) ? params : null;
  }

  /**
   * Default component values ("c1"/"c2") for empty slots, from the
   * `save_directkey` catalog entry's introspected param defaults
   * (§9.3.5 — the DEFAULT_C1/C2 hand-copy is dead) with the hardcoded
   * service-schema mirror as the §5.3.6 fallback tier.
   */
  _componentDefaults(slot) {
    const n = slot === "c2" ? "2" : "1";
    const fallback = slot === "c2" ? FALLBACK_DEFAULT_C2 : FALLBACK_DEFAULT_C1;
    const params = this._saveDirectkeyParams();
    if (!params) return fallback;
    const fields = {
      [`process${n}`]: "process",
      [`intensity${n}`]: "intensity",
      [`aroma${n}`]: "aroma",
      [`temperature${n}`]: "temperature",
      [`shots${n}`]: "shots",
      [`portion${n}_ml`]: "portion_ml",
    };
    const defaults = { ...fallback };
    for (const param of params) {
      const field = fields[param?.name];
      if (field && param.default !== undefined && param.default !== null) {
        defaults[field] = param.default;
      }
    }
    return defaults;
  }

  // ── labels ─────────────────────────────────────────────────────────

  /**
   * DirectKey category token for a recipe row: the served `category`
   * field (§9.3.4 — the server derives it from the enum) with the
   * positional fixture as fallback for rows without one.
   */
  _categoryToken(recipe, index) {
    if (typeof recipe?.category === "string" && recipe.category) {
      return recipe.category;
    }
    return DIRECTKEY_CATEGORIES[index] || null;
  }

  /**
   * DirectKey category label (§6.3.5): server string
   * `values.directkey_category.<token>` → panel bundle
   * (`recipes.cat.<token>`) → provided fallback / raw token.
   */
  _categoryLabel(token, fallback) {
    const key = `recipes.cat.${token}`;
    const value = this._t(key);
    const bundled = value === key ? (fallback || token) : value;
    return labelFor(`values.directkey_category.${token}`, bundled, token);
  }

  /**
   * Family-scoped label for a vocabulary token (§6.3.5): server string
   * `values.<family>.<token>` → panel bundle (`recipes.opt.<token>`) →
   * humanized token.
   */
  _optionLabel(family, token) {
    const key = `recipes.opt.${token}`;
    const value = this._t(key);
    return displayNameFor(family, token, value === key ? null : value);
  }

  // ── service plumbing ───────────────────────────────────────────────

  /**
   * Entity id of a button belonging to this machine, for entity-targeted
   * services. Prefers device-registry scoping to the active config entry;
   * falls back to the brew wizard's suffix convention.
   */
  _findServiceEntity() {
    const states = this.hass?.states || {};
    const entities = this.hass?.entities || {};
    const devices = this.hass?.devices || {};
    const buttons = Object.keys(states).filter((id) => id.startsWith("button."));
    const scoped = buttons.find((id) => {
      const device = devices[entities[id]?.device_id];
      return Boolean(device?.config_entries?.includes?.(this.entryId));
    });
    if (scoped) return scoped;
    const ids = buttons.filter((id) => id.endsWith("confirm_prompt"));
    if (ids.length <= 1) return ids[0] || null;
    return ids.find((id) => entities[id]?.platform === "melitta_barista") || ids[0];
  }

  /** Surface a toast via the panel shell's shared <melitta-toast>. */
  _showToast(message, kind = "success") {
    const toast = document.querySelector("melitta-panel")
      ?.renderRoot?.querySelector?.("#toast")
      || document.querySelector("melitta-toast");
    toast?.show?.(message, kind);
  }

  /** Open <melitta-confirm> and await user decision. */
  async _confirmReset() {
    let dialog = this.renderRoot.querySelector("melitta-confirm");
    if (!dialog) {
      dialog = document.createElement("melitta-confirm");
      this.renderRoot.appendChild(dialog);
    }
    return dialog.ask({
      title: this._t("recipes.reset_confirm_title"),
      message: this._t("recipes.reset_confirm"),
      confirmLabel: this._t("recipes.reset_default"),
      cancelLabel: this._t("common.cancel"),
      destructive: true,
    });
  }

  // ── editing ────────────────────────────────────────────────────────

  _componentForm(comp, defaults) {
    const src = comp && typeof comp === "object" ? comp : {};
    return {
      process: src.process || defaults.process,
      intensity: src.intensity || defaults.intensity,
      aroma: src.aroma || defaults.aroma,
      temperature: src.temperature || defaults.temperature,
      shots: src.shots || defaults.shots,
      portion_ml: Number.isFinite(Number(src.portion_ml))
        ? Number(src.portion_ml)
        : defaults.portion_ml,
    };
  }

  _openEditor(recipe, index) {
    if (!this._connected()) return;
    const category = this._categoryToken(recipe, index);
    if (!category) return;
    const components = recipe?.components || [];
    this._editing = {
      category,
      recipeId: recipe?.id ?? null,
      name: recipe?.name || this._categoryLabel(category),
      icon: recipe?.icon || null,
      empty: !components[0],
      c1: this._componentForm(components[0], this._componentDefaults("c1")),
      c2: this._componentForm(components[1], this._componentDefaults("c2")),
    };
  }

  _closeEditor() {
    this._editing = null;
  }

  _updateComponent(slot, key, value) {
    if (!this._editing) return;
    this._editing = {
      ...this._editing,
      [slot]: { ...this._editing[slot], [key]: value },
    };
  }

  async _save() {
    const e = this._editing;
    if (!e || this._busy) return;
    const entityId = this._findServiceEntity();
    if (!entityId) {
      this._error = this._t("recipes.no_entity");
      return;
    }
    const c1 = e.c1;
    const c2 = e.c2;
    this._busy = true;
    try {
      await this.hass.callService("melitta_barista", "save_directkey", {
        entity_id: entityId,
        category: e.category,
        profile_id: this._profileId,
        process1: c1.process === "none" ? "coffee" : c1.process,
        intensity1: c1.intensity,
        aroma1: c1.aroma,
        temperature1: c1.temperature,
        shots1: c1.shots,
        portion1_ml: this._clampPortion(c1.portion_ml, "c1"),
        process2: c2.process,
        intensity2: c2.intensity,
        aroma2: c2.aroma,
        temperature2: c2.temperature,
        shots2: c2.shots,
        portion2_ml: this._clampPortion(c2.portion_ml, "c2"),
      });
      this._closeEditor();
      await this._load();
      this._showToast(
        this._t("recipes.save_success", { category: this._categoryLabel(e.category) }),
        "success",
      );
      this._error = "";
    } catch (err) {
      this._showToast(
        `${this._t("recipes.save_failed")}: ${err?.message || err}`,
        "error",
      );
    } finally {
      this._busy = false;
    }
  }

  async _reset(recipe, index) {
    if (this._busy || !this._connected()) return;
    const category = this._categoryToken(recipe, index);
    if (!Number.isFinite(Number(recipe?.id))) return;
    if (!(await this._confirmReset())) return;
    const entityId = this._findServiceEntity();
    if (!entityId) {
      this._error = this._t("recipes.no_entity");
      return;
    }
    this._busy = true;
    try {
      await this.hass.callService("melitta_barista", "reset_recipe", {
        entity_id: entityId,
        recipe_id: Number(recipe.id),
      });
      this._closeEditor();
      await this._load();
      this._showToast(
        this._t("recipes.reset_success", { category: this._categoryLabel(category) }),
        "success",
      );
      this._error = "";
    } catch (err) {
      this._showToast(
        `${this._t("recipes.reset_failed")}: ${err?.message || err}`,
        "error",
      );
    } finally {
      this._busy = false;
    }
  }

  // ── rendering: category grid ───────────────────────────────────────

  _componentSummary(comp) {
    if (!comp || comp.process === "none") return null;
    const parts = [this._optionLabel("process", comp.process)];
    if (comp.portion_ml) parts.push(`${comp.portion_ml} ml`);
    if (comp.process === "coffee" && comp.intensity) {
      parts.push(this._optionLabel("intensity", comp.intensity));
    }
    return parts.join(" · ");
  }

  _renderCategoryCard(recipe, index) {
    const token = this._categoryToken(recipe, index);
    const connected = this._connected();
    const s1 = this._componentSummary(recipe.components?.[0]);
    const s2 = this._componentSummary(recipe.components?.[1]);
    return html`
      <button
        class="drink-card"
        ?disabled=${!connected || this._busy}
        title=${connected ? "" : this._t("recipes.disconnected")}
        @click=${() => this._openEditor(recipe, index)}
      >
        <melitta-drink-icon .spec=${recipe.icon} size="40"></melitta-drink-icon>
        <span class="drink-name">${this._categoryLabel(token, recipe.name)}</span>
        ${s1 ? html`<span class="drink-summary">${s1}</span>` : ""}
        ${s2 ? html`<span class="drink-summary">${s2}</span>` : ""}
      </button>
    `;
  }

  _renderProfilePicker() {
    const profiles = this._profiles();
    if (profiles.length === 0) return "";
    return html`
      <label class="profile-picker">
        ${this._t("recipes.profile")}
        <select @change=${(e) => { this._profileId = Number(e.target.value); }}>
          ${profiles.map((p) => html`
            <option value=${p.profile_id} ?selected=${p.profile_id === this._profileId}>
              ${p.profile_name || `Profile ${p.profile_id}`}
            </option>
          `)}
        </select>
      </label>
    `;
  }

  // ── rendering: edit modal ──────────────────────────────────────────

  _renderSelect(slot, key, options, disabled) {
    const current = this._editing?.[slot]?.[key];
    return html`
      <label>${this._t(`recipes.${key}`)}
        <select ?disabled=${disabled}
          @change=${(e) => this._updateComponent(slot, key, e.target.value)}>
          ${options.map((opt) => html`
            <option value=${opt} ?selected=${opt === current}>
              ${this._optionLabel(key, opt)}
            </option>
          `)}
        </select>
      </label>
    `;
  }

  _renderPortion(slot, comp, disabled) {
    const { min, max, step } = this._portionLimits(comp);
    const value = this._editing?.[slot]?.portion_ml ?? min;
    return html`
      <label>${this._t("recipes.portion")}
        <input type="number" min=${min} max=${max} step=${step}
          .value=${String(value)} ?disabled=${disabled}
          @change=${(e) => this._updateComponent(
            slot, "portion_ml", this._clampPortion(e.target.value, comp),
          )} />
      </label>
    `;
  }

  _renderComponentBlock(slot, titleKey) {
    const isC2 = slot === "c2";
    const comp = isC2 ? "c2" : "c1";
    const form = this._editing?.[slot];
    const processOptions = isC2
      ? this._vocab("process")
      : this._vocab("process").filter((p) => p !== "none");
    const rest = isC2 && form?.process === "none";
    return html`
      <fieldset class="component">
        <legend>${this._t(titleKey)}</legend>
        <div class="grid3">
          ${this._renderSelect(slot, "process", processOptions, false)}
          ${this._renderSelect(slot, "intensity", this._vocab("intensity"), rest)}
          ${this._renderSelect(slot, "aroma", this._vocab("aroma"), rest)}
        </div>
        <div class="grid3">
          ${this._renderSelect(slot, "temperature", this._vocab("temperature"), rest)}
          ${this._renderSelect(slot, "shots", this._vocab("shots"), rest)}
          ${this._renderPortion(slot, comp, rest)}
        </div>
      </fieldset>
    `;
  }

  _renderEditor() {
    const e = this._editing;
    if (!e) return "";
    return html`
      <melitta-modal .open=${true}
        .title=${this._t("recipes.edit_title", { category: this._categoryLabel(e.category, e.name) })}
        @close=${() => this._closeEditor()}>
        <div class="form">
          <div class="editor-head">
            <melitta-drink-icon .spec=${e.icon} size="48"></melitta-drink-icon>
            ${e.empty ? html`<span class="hint small">${this._t("recipes.empty_slot")}</span>` : ""}
          </div>
          ${this._renderComponentBlock("c1", "recipes.component1")}
          ${this._renderComponentBlock("c2", "recipes.component2")}
          <div class="form-actions">
            <button class="ghost danger" ?disabled=${this._busy || e.recipeId == null}
              @click=${() => this._reset({ id: e.recipeId, name: e.name }, 0)}>
              ${this._t("recipes.reset_default")}
            </button>
            <span class="spacer"></span>
            <button class="ghost" ?disabled=${this._busy}
              @click=${() => this._closeEditor()}>${this._t("common.cancel")}</button>
            <button class="primary" ?disabled=${this._busy}
              @click=${() => this._save()}>
              ${this._busy ? this._t("common.loading") : this._t("common.save")}
            </button>
          </div>
        </div>
      </melitta-modal>
    `;
  }

  // ── rendering: read-only base-recipe reference ─────────────────────

  _renderComponentCell(comp) {
    if (!comp || comp.process === "none") return html`<span class="dim">—</span>`;
    return html`
      <div class="comp">
        <span class="proc">${this._optionLabel("process", comp.process)}</span>
        ${comp.portion_ml ? html`<span class="ml">${comp.portion_ml} ml</span>` : ""}
        ${comp.intensity && comp.intensity !== "medium" ? html`<span class="badge">${this._optionLabel("intensity", comp.intensity)}</span>` : ""}
        ${comp.aroma && comp.aroma !== "standard" ? html`<span class="badge">${this._optionLabel("aroma", comp.aroma)}</span>` : ""}
        ${comp.temperature && comp.temperature !== "normal" ? html`<span class="badge">${this._optionLabel("temperature", comp.temperature)}</span>` : ""}
        ${comp.shots && comp.shots !== "none" ? html`<span class="badge">${this._optionLabel("shots", comp.shots)}</span>` : ""}
      </div>
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
        <tbody>
          ${rows.map((r) => html`
            <tr>
              <td class="id">${r.id}</td>
              <td class="name-cell">
                <melitta-drink-icon .spec=${r.icon} size="22"></melitta-drink-icon>
                ${r.name || html`<span class="dim">—</span>`}
              </td>
              <td>${this._renderComponentCell(r.components?.[0])}</td>
              <td>${this._renderComponentCell(r.components?.[1])}</td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  // ── render ─────────────────────────────────────────────────────────

  render() {
    if (this._error && !this._data) {
      return html`<div class="error">${this._t("common.error")}: ${this._error}</div>`;
    }
    if (!this._data && this._loading) {
      return html`<div class="hint">${this._t("common.loading")}</div>`;
    }
    const profile = this._profile();
    return html`
      <section class="card">
        <header class="card-head">
          <h2>${this._t("recipes.editor_title")}</h2>
          <div class="head-actions">
            ${this._renderProfilePicker()}
            <button class="action" ?disabled=${this._loading}
              @click=${() => this._load()}>${this._t("common.refresh")}</button>
          </div>
        </header>

        ${this._connected() ? "" : html`
          <div class="notice">${this._t("recipes.disconnected")}</div>
        `}
        ${this._error ? html`<div class="error">${this._t("common.error")}: ${this._error}</div>` : ""}

        <p class="hint small">${this._t("recipes.editor_hint")}</p>

        ${profile && profile.recipes?.length
          ? html`
            <div class="drink-grid">
              ${profile.recipes.map((r, i) => this._renderCategoryCard(r, i))}
            </div>
          `
          : html`<div class="hint">${this._t("recipes.no_data")}</div>`}

        <details class="base-recipes">
          <summary>${this._t("recipes.base_recipes")}</summary>
          ${this._renderBaseRecipes(this._data?.base_recipes)}
        </details>

        ${this._renderEditor()}
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
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 8px;
      }
      .head-actions {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      h2 { margin: 0; font-size: 18px; }

      .profile-picker {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        color: var(--secondary-text-color);
      }
      .profile-picker select {
        padding: 6px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
      }

      .notice {
        margin: 12px 0;
        padding: 10px 14px;
        background: var(--warning-color, #ff9800);
        color: var(--text-primary-color);
        border-radius: 4px;
        font-size: 13px;
      }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      .hint.small { font-size: 12px; margin: 4px 0 12px; }
      .dim { color: var(--secondary-text-color); }

      .drink-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
        gap: 10px;
        margin: 8px 0 16px;
      }
      .drink-card {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        padding: 14px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
        font-family: inherit;
        text-align: center;
      }
      .drink-card:hover:not(:disabled) {
        border-color: var(--primary-color);
      }
      .drink-card:disabled { opacity: 0.55; cursor: not-allowed; }
      .drink-name { font-size: 13px; font-weight: 500; }
      .drink-summary {
        font-size: 11px;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }

      /* editor form (inside modal) */
      .form { display: flex; flex-direction: column; gap: 12px; }
      .editor-head {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      fieldset.component {
        border: 1px solid var(--divider-color);
        border-radius: 6px;
        padding: 8px 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      fieldset.component legend {
        padding: 0 4px;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .grid3 {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 8px;
      }
      .form label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .form select, .form input {
        padding: 7px 8px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
        font-family: inherit;
      }
      .form select:disabled, .form input:disabled { opacity: 0.5; }
      .form-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 4px;
      }
      .form-actions .spacer { flex: 1; }

      button.primary {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.primary:hover:not(:disabled) { opacity: 0.9; }
      button.ghost {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.ghost.danger { color: var(--error-color); border-color: var(--error-color); }
      button.action {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.action:hover:not(:disabled) { opacity: 0.9; }
      button:disabled { opacity: 0.5; cursor: not-allowed; }

      /* base recipes reference */
      details.base-recipes {
        margin-top: 8px;
        border-top: 1px solid var(--divider-color);
        padding-top: 8px;
      }
      details.base-recipes summary {
        cursor: pointer;
        font-size: 13px;
        color: var(--secondary-text-color);
      }
      table.recipes {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        margin-top: 8px;
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
      td.name-cell {
        white-space: nowrap;
      }
      td.name-cell melitta-drink-icon {
        vertical-align: middle;
        margin-right: 6px;
      }
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
    `;
  }
}

if (!customElements.get('melitta-recipes')) customElements.define('melitta-recipes', MelittaRecipes);
