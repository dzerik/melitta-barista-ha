/**
 * <melitta-brew-wizard> — linear step-machine wizard for brewing a
 * sommelier recipe.
 *
 * Opens on "Brew this" in melitta-sommelier. The recipe JSON is compiled
 * into ONE flat, numbered checklist:
 *
 *   [cup step (synthesized from cup_type)]
 *   + steps with phase "pre" (sorted by order)
 *   + for each machine_phases[i]:
 *       its user_action_before entries as manual steps,
 *       then a machine step that fires ONE per-phase brew
 *       (WS melitta_barista/sommelier/brew_phase { phase_index: i })
 *   + steps with phase "post".
 *
 * Exactly one step is active at a time. Manual steps advance only on an
 * explicit "Done" tap; machine steps poll melitta_barista/status and
 * auto-advance on the nested status.is_brewing true→false transition
 * (a completion is only trusted after is_brewing was OBSERVED true, so
 * the warm-up window can't be mistaken for "done"). status.progress
 * drives the bar when the machine reports a usable percentage; a
 * time-based estimate fills in otherwise. Machine confirmation prompts
 * (status.awaiting_confirmation) surface as an inline card wired to the
 * melitta_barista.confirm_prompt service.
 *
 * Re-entry: the current position is persisted per recipe id in
 * localStorage; closing mid-brew warns (the machine keeps pouring) and
 * reopening the wizard resumes where the user left off. Steps with
 * phase "during" are shown as hints on the first machine step.
 *
 * Legacy rows without machine_phases fall back to a single full-recipe
 * brew step via the original brew / favorites/brew commands.
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";
import { t } from "../i18n/index.js";
import { displayNameFor, labelFor } from "../i18n/server-strings.js";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_BUFFER_S = 30;
const PROGRESS_CAP_PERCENT = 95;
const RESUME_TTL_MS = 2 * 60 * 60 * 1000; // 2h — stale positions restart.
const STORAGE_PREFIX = "melitta_barista.wizard.";

/** Estimated seconds for one machine phase: warmup + pump time by volume. */
function estimatePhaseSeconds(component) {
  const ml = Number(component?.portion_ml) || 0;
  return Math.max(10, Math.round(8 + (ml / 50) * 5));
}

/** Fresh per-machine-step state (one brew attempt lifecycle). */
function freshMachineState() {
  return {
    state: "idle", // idle | brewing | error
    error: "",
    progress: 0,
    liveProgress: false,
    sawBrewing: false,
    manualFinish: false,
    prompt: null,
    confirmBusy: false,
    confirmError: "",
    startedAt: 0,
    estimated: 0,
  };
}

class MelittaBrewWizard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      recipe: { type: Object },
      open: { type: Boolean, reflect: true },
      source: { type: String },
      sourceId: { type: String },
      canBrew: { type: Boolean },
      serverStrings: { attribute: false },
      _steps: { state: true },
      _stepIndex: { state: true },
      _machine: { state: true },
      _confirmClose: { state: true },
      _resumed: { state: true },
    };
  }

  constructor() {
    super();
    this.recipe = null;
    this.open = false;
    this.source = "generated";
    this.sourceId = null;
    // Optimistic default — the parent overrides when capabilities say
    // the machine is print-only (Nivona recipe_writes gate).
    this.canBrew = true;
    this.serverStrings = null;
    this._steps = [];
    this._stepIndex = 0;
    this._machine = freshMachineState();
    this._confirmClose = false;
    this._resumed = false;
    this._pollHandle = null;
    this._tickHandle = null;
  }

  _t(key, params) { return t(key, this.lang || "en", params); }

  /**
   * Family-scoped value-token label (UI Contract §6.3.5): server string
   * `values.<family>.<token>` → panel bundle (`recipes.opt.<token>`) →
   * humanized token.
   */
  _valueLabel(family, token) {
    const bundleKey = `recipes.opt.${token}`;
    const bundled = this._t(bundleKey);
    return displayNameFor(family, token, bundled === bundleKey ? null : bundled);
  }

  // ── Step model ─────────────────────────────────────────────────────

  /** Localized cup label; falls back to the raw cup_type for unknown values. */
  _cupLabel(cupType) {
    const key = `sommelier.cup.${cupType}`;
    const translated = t(key, this.lang || "en");
    return translated === key ? cupType : translated;
  }

  /** Compile the recipe JSON into the flat ordered step list. */
  _buildSteps() {
    const r = this.recipe;
    if (!r) return [];
    const byOrder = (a, b) => (a.order || 0) - (b.order || 0);
    const all = Array.isArray(r.steps) ? r.steps : [];
    const pre = all.filter((s) => (s.phase || "during") === "pre").sort(byOrder);
    const during = all.filter((s) => (s.phase || "during") === "during").sort(byOrder);
    const post = all.filter((s) => (s.phase || "during") === "post").sort(byOrder);
    const phases = Array.isArray(r.machine_phases) ? r.machine_phases : [];

    const steps = [];
    if (r.cup_type) {
      const totalMl = phases.reduce(
        (acc, p) => acc + (Number(p?.component?.portion_ml) || 0), 0
      ) || (Number(r.component1?.portion_ml) || 0) + (Number(r.component2?.portion_ml) || 0);
      steps.push({
        kind: "manual",
        synthetic: true,
        text: this._t("wizard.step.cup", { ml: totalMl, cup: this._cupLabel(r.cup_type) }),
      });
    }
    for (const s of pre) steps.push({ kind: "manual", step: s });

    let pourN = 0;
    if (phases.length) {
      phases.forEach((p, i) => {
        // The brew_phase reply's manual_actions_next mirrors exactly
        // this list for the FOLLOWING phase — the wizard shows it here,
        // before the next brew_phase call, straight from the recipe.
        const actions = Array.isArray(p.user_action_before)
          ? [...p.user_action_before].sort(byOrder) : [];
        for (const s of actions) steps.push({ kind: "manual", step: s });
        pourN += 1;
        steps.push({
          kind: "machine",
          phaseIndex: i,
          pourN,
          component: p.component || {},
          hints: i === 0 ? during : [],
        });
      });
    } else if (r.component1) {
      // Legacy row without machine_phases: one full-recipe brew.
      pourN = 1;
      steps.push({
        kind: "machine",
        legacyFull: true,
        pourN,
        component: r.component1 || {},
        hints: during,
      });
    }
    for (const step of steps) if (step.kind === "machine") step.pourCount = pourN;
    for (const s of post) steps.push({ kind: "manual", step: s });
    return steps;
  }

  // ── Lifecycle / re-entry ───────────────────────────────────────────

  willUpdate(changed) {
    const opened = changed.has("open") && this.open;
    const recipeChanged = changed.has("recipe") && this.recipe;
    if ((opened || recipeChanged) && this.open && this.recipe) {
      this._resetForRecipe();
    }
    if (changed.has("open") && !this.open) this._stopPolling();
  }

  _storageKey() {
    const id = this.recipe?.id || this.sourceId;
    return id ? `${STORAGE_PREFIX}${id}` : null;
  }

  _loadSaved() {
    const key = this._storageKey();
    if (!key) return null;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (!saved || typeof saved.stepIndex !== "number") return null;
      if (Date.now() - (saved.ts || 0) > RESUME_TTL_MS) return null;
      return saved;
    } catch (e) {
      return null;
    }
  }

  _saveProgress() {
    const key = this._storageKey();
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify({ stepIndex: this._stepIndex, ts: Date.now() }));
    } catch (e) { /* private mode / quota — resume is best-effort */ }
  }

  _clearProgress() {
    const key = this._storageKey();
    if (!key) return;
    try { localStorage.removeItem(key); } catch (e) { /* best-effort */ }
  }

  /** Rebuild steps and restore a saved position for this recipe, if any. */
  _resetForRecipe() {
    this._stopPolling();
    this._steps = this._buildSteps();
    this._stepIndex = 0;
    this._machine = freshMachineState();
    this._confirmClose = false;
    this._resumed = false;
    const saved = this._loadSaved();
    if (saved && saved.stepIndex > 0 && saved.stepIndex <= this._steps.length) {
      this._stepIndex = saved.stepIndex;
      this._resumed = true;
    }
  }

  _restart() {
    this._stopPolling();
    this._stepIndex = 0;
    this._machine = freshMachineState();
    this._resumed = false;
    this._clearProgress();
  }

  // ── Navigation ─────────────────────────────────────────────────────

  get _finished() {
    return this._steps.length > 0 && this._stepIndex >= this._steps.length;
  }

  _advance() {
    this._machine = freshMachineState();
    this._stepIndex = Math.min(this._stepIndex + 1, this._steps.length);
    if (this._finished) this._clearProgress();
    else this._saveProgress();
  }

  _requestClose() {
    const started = this._stepIndex > 0 || this._machine.state !== "idle";
    if (started && !this._finished) {
      this._confirmClose = true;
      return;
    }
    this._close();
  }

  _close() {
    this._stopPolling();
    this._confirmClose = false;
    this.open = false;
    this.dispatchEvent(new CustomEvent("close", { bubbles: true, composed: true }));
  }

  _finishAndClose() {
    this._clearProgress();
    this._close();
  }

  // ── Machine step: brew + polling ───────────────────────────────────

  _setMachine(patch) {
    this._machine = { ...this._machine, ...patch };
  }

  async _startMachineStep(step) {
    if (!this.canBrew) return;
    this._setMachine({
      ...freshMachineState(),
      state: "brewing",
      startedAt: Date.now(),
      estimated: step.legacyFull
        ? (this.recipe?.machine_phases || [step]).reduce(
            (acc, p) => acc + estimatePhaseSeconds(p.component), 0
          ) || estimatePhaseSeconds(step.component)
        : estimatePhaseSeconds(step.component),
    });
    // Scope the brew to the machine this panel instance targets — without
    // entry_id the backend falls back to the FIRST config entry, which is
    // the wrong machine on multi-machine installs.
    const scope = this.entryId ? { entry_id: this.entryId } : {};
    try {
      if (step.legacyFull) {
        const call = this.source === "favorite"
          ? { type: "melitta_barista/sommelier/favorites/brew", favorite_id: this.sourceId || this.recipe.id, ...scope }
          : { type: "melitta_barista/sommelier/brew", recipe_id: this.sourceId || this.recipe.id, ...scope };
        await this.hass.callWS(call);
      } else {
        const target = this.source === "favorite"
          ? { favorite_id: this.sourceId || this.recipe.id }
          : { recipe_id: this.sourceId || this.recipe.id };
        await this.hass.callWS({
          type: "melitta_barista/sommelier/brew_phase",
          ...target,
          ...scope,
          phase_index: step.phaseIndex,
        });
      }
    } catch (e) {
      const code = e?.code || "";
      const msg = code === "recipe_writes_unsupported"
        ? this._t("brewing.unsupported_error")
        : (e?.message || String(e));
      this._setMachine({ state: "error", error: msg });
      return;
    }
    this._startPolling();
  }

  _startPolling() {
    this._stopPolling();
    this._tickHandle = setInterval(() => this._tick(), 250);
    this._pollHandle = setInterval(() => this._pollStatus(), POLL_INTERVAL_MS);
  }

  _stopPolling() {
    if (this._pollHandle) { clearInterval(this._pollHandle); this._pollHandle = null; }
    if (this._tickHandle) { clearInterval(this._tickHandle); this._tickHandle = null; }
  }

  /** Time-driven fallback: estimate-based progress + manual-finish escape. */
  _tick() {
    const m = this._machine;
    if (m.state !== "brewing") return;
    const elapsed = (Date.now() - m.startedAt) / 1000;
    const patch = {};
    if (!m.liveProgress && m.estimated > 0) {
      patch.progress = Math.min(PROGRESS_CAP_PERCENT, (elapsed / m.estimated) * 100);
    }
    if (elapsed > m.estimated + POLL_TIMEOUT_BUFFER_S) patch.manualFinish = true;
    this._setMachine(patch);
  }

  async _pollStatus() {
    let payload;
    try {
      payload = await this.hass.callWS({
        type: "melitta_barista/status",
        entry_id: this.entryId,
      });
    } catch (e) {
      this._setMachine({ manualFinish: true });
      return;
    }
    const st = payload?.status;
    if (!st || this._machine.state !== "brewing") return;
    // Manipulation prompt text per §6.3.5: server string
    // (status.manipulation.<TOKEN>) → humanized token fallback.
    const patch = {
      prompt: st.awaiting_confirmation === true
        ? (st.manipulation
            ? labelFor(`status.manipulation.${st.manipulation}`, null, st.manipulation)
            : this._t("wizard.machine.prompt_generic"))
        : null,
    };
    if (st.is_brewing === true) {
      patch.sawBrewing = true;
      const p = Number(st.progress);
      if (Number.isFinite(p) && p > 0 && p <= 100) {
        patch.liveProgress = true;
        patch.progress = p;
      }
    }
    // Only trust "not brewing" as completion after we SAW the machine
    // brewing — the first polls land during warm-up, before PRODUCT.
    if (this._machine.sawBrewing && st.is_brewing === false) {
      this._stopPolling();
      this._setMachine({ progress: 100 });
      this._advance();
      return;
    }
    this._setMachine(patch);
  }

  // ── Machine prompt confirmation ────────────────────────────────────

  /** Entity id of the machine's Confirm Prompt button, or null. */
  _findConfirmEntity() {
    const states = this.hass?.states || {};
    const ids = Object.keys(states).filter(
      (id) => id.startsWith("button.") && id.endsWith("confirm_prompt")
    );
    if (ids.length <= 1) return ids[0] || null;
    const registry = this.hass?.entities || {};
    return ids.find((id) => registry[id]?.platform === "melitta_barista") || ids[0];
  }

  async _confirmPrompt() {
    const entityId = this._findConfirmEntity();
    if (!entityId) {
      this._setMachine({ confirmError: this._t("wizard.machine.confirm_manual") });
      return;
    }
    this._setMachine({ confirmBusy: true, confirmError: "" });
    try {
      await this.hass.callService("melitta_barista", "confirm_prompt", { entity_id: entityId });
      this._setMachine({ prompt: null, confirmBusy: false });
    } catch (e) {
      this._setMachine({
        confirmBusy: false,
        confirmError: `${this._t("wizard.machine.confirm_failed")}: ${e?.message || e}`,
      });
    }
  }

  // ── Rendering ──────────────────────────────────────────────────────

  _stepTitle(step) {
    if (step.kind === "machine") {
      return step.pourCount > 1
        ? this._t("wizard.step.machine_n", { n: step.pourN, m: step.pourCount })
        : this._t("wizard.step.machine");
    }
    if (step.synthetic) return step.text;
    const s = step.step || {};
    const qty = (s.amount && s.unit) ? ` (${s.amount} ${s.unit})` : "";
    return `${s.action || ""}${qty}${s.ingredient ? ` — ${s.ingredient}` : ""}`;
  }

  _renderManualDetails(step) {
    if (step.synthetic) return "";
    const s = step.step || {};
    return s.notes ? html`<div class="note">${s.notes}</div>` : "";
  }

  _renderComponentBadges(component) {
    const c = component || {};
    const badges = [];
    if (c.process) badges.push(this._valueLabel("process", c.process));
    if (c.portion_ml) badges.push(`${c.portion_ml} ml`);
    if (c.shots) badges.push(`×${c.shots}`);
    if (c.intensity) badges.push(this._valueLabel("intensity", c.intensity));
    return badges.length
      ? html`<div class="badges">${badges.map((b) => html`<span class="badge">${b}</span>`)}</div>`
      : "";
  }

  _renderPromptCard() {
    const m = this._machine;
    if (!m.prompt) return "";
    const entityId = this._findConfirmEntity();
    return html`
      <div class="prompt-card">
        <div>${this._t("wizard.machine.prompt", { prompt: m.prompt })}</div>
        ${entityId
          ? html`<button class="primary" ?disabled=${m.confirmBusy}
                    @click=${() => this._confirmPrompt()}>
                    ${this._t("wizard.machine.confirm")}
                  </button>`
          : html`<div class="note">${this._t("wizard.machine.confirm_manual")}</div>`}
        ${m.confirmError ? html`<div class="error">${m.confirmError}</div>` : ""}
      </div>
    `;
  }

  _renderMachineActive(step) {
    const m = this._machine;
    if (!this.canBrew) {
      return html`
        ${this._renderComponentBadges(step.component)}
        <div class="unsupported-note">${this._t("brewing.unsupported_note")}</div>
        <div class="actions">
          <button class="ghost" @click=${() => this._advance()}>${this._t("wizard.machine.skip")}</button>
        </div>
      `;
    }
    if (m.state === "idle") {
      return html`
        ${this._renderComponentBadges(step.component)}
        ${step.hints.length ? html`
          <div class="hints">
            <div class="hints-title">${this._t("wizard.machine.during_hint")}</div>
            <ul>${step.hints.map((s) => html`<li>${this._stepTitle({ kind: "manual", step: s })}</li>`)}</ul>
          </div>` : ""}
        <div class="actions">
          <button class="primary big" @click=${() => this._startMachineStep(step)}>
            ${step.legacyFull ? this._t("wizard.machine.start_full") : this._t("wizard.machine.start")}
          </button>
        </div>
      `;
    }
    if (m.state === "error") {
      return html`
        <div class="error">${this._t("wizard.machine.failed")}: ${m.error}</div>
        <div class="actions">
          <button class="ghost" @click=${() => this._advance()}>${this._t("wizard.machine.skip")}</button>
          <button class="primary" @click=${() => this._startMachineStep(step)}>
            ${this._t("wizard.machine.retry")}
          </button>
        </div>
      `;
    }
    // brewing
    return html`
      <div class="progress-row">
        <div class="progress-bar"><div class="progress-fill" style="width: ${m.progress}%"></div></div>
        <span class="progress-pct">${Math.round(m.progress)}%</span>
      </div>
      <p class="muted">${this._t("wizard.machine.estimated", { sec: m.estimated })}</p>
      ${step.hints.length ? html`
        <div class="hints">
          <div class="hints-title">${this._t("wizard.machine.during_hint")}</div>
          <ul>${step.hints.map((s) => html`<li>${this._stepTitle({ kind: "manual", step: s })}</li>`)}</ul>
        </div>` : ""}
      ${this._renderPromptCard()}
      <div class="actions">
        ${m.manualFinish
          ? html`<button class="primary" @click=${() => { this._stopPolling(); this._advance(); }}>
                   ${this._t("wizard.machine.im_done")}
                 </button>`
          : html`<span class="muted">${this._t("wizard.machine.waiting")}</span>`}
      </div>
    `;
  }

  _renderStep(step, i) {
    const stateCls = i < this._stepIndex ? "done" : i === this._stepIndex ? "active" : "future";
    return html`
      <li class="step ${stateCls}">
        <span class="bullet">${i < this._stepIndex ? "✓" : i + 1}</span>
        <div class="step-body">
          <div class="step-title">${this._stepTitle(step)}</div>
          ${i === this._stepIndex ? html`
            <div class="step-card">
              ${step.kind === "manual" ? html`
                ${this._renderManualDetails(step)}
                <div class="actions">
                  <button class="primary big" @click=${() => this._advance()}>
                    ${this._t("wizard.step.done")}
                  </button>
                </div>
              ` : this._renderMachineActive(step)}
            </div>
          ` : ""}
        </div>
      </li>
    `;
  }

  _renderFinish() {
    const instruction = this.recipe?.extras?.instruction;
    return html`
      <div class="finish">
        <h3>${this._t("wizard.finish.title")}</h3>
        ${instruction ? html`<p class="instruction">${instruction}</p>` : ""}
        <p class="muted">${this._t("wizard.finish.message")}</p>
        <div class="actions">
          <button class="primary big" @click=${() => this._finishAndClose()}>
            ${this._t("wizard.finish.button")}
          </button>
        </div>
      </div>
    `;
  }

  _renderConfirmClose() {
    return html`
      <div class="confirm-overlay">
        <div class="confirm-box">
          <h3>${this._t("wizard.close.title")}</h3>
          <p>${this._t("wizard.close.message")}</p>
          <div class="actions">
            <button class="ghost" @click=${() => { this._confirmClose = false; }}>
              ${this._t("wizard.close.stay")}
            </button>
            <button class="primary" @click=${() => { this._saveProgress(); this._close(); }}>
              ${this._t("wizard.close.leave")}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  render() {
    if (!this.open || !this.recipe) return html``;
    const total = this._steps.length;
    const current = Math.min(this._stepIndex + 1, Math.max(total, 1));
    return html`
      <div class="backdrop" @click=${(e) => { if (e.target === e.currentTarget) this._requestClose(); }}>
        <div class="dialog" role="dialog" aria-modal="true">
          <header>
            <div class="head-text">
              <h2>${this.recipe?.name || this._t("wizard.title")}</h2>
              ${total ? html`<span class="step-count">${this._t("wizard.step_of", { n: current, m: total })}</span>` : ""}
            </div>
            <button class="close" @click=${() => this._requestClose()}>×</button>
          </header>
          <div class="body">
            ${this._resumed && !this._finished ? html`
              <div class="resume-banner">
                <span>${this._t("wizard.resumed")}</span>
                <button class="ghost" @click=${() => this._restart()}>${this._t("wizard.restart")}</button>
              </div>` : ""}
            ${this._finished || total === 0
              ? this._renderFinish()
              : html`<ol class="steps">${this._steps.map((s, i) => this._renderStep(s, i))}</ol>`}
          </div>
          ${this._confirmClose ? this._renderConfirmClose() : ""}
        </div>
      </div>
    `;
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling();
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        .backdrop {
          position: fixed; inset: 0; background: rgba(0,0,0,0.55);
          display: flex; align-items: center; justify-content: center;
          z-index: 100;
        }
        .dialog {
          position: relative;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          border-radius: var(--mb-radius-md);
          min-width: min(360px, calc(100vw - 32px));
          max-width: min(640px, 92vw);
          max-height: 86vh;
          display: flex; flex-direction: column;
          box-shadow: 0 12px 32px rgba(0,0,0,0.4);
        }
        header {
          display: flex; align-items: center; justify-content: space-between;
          padding: var(--mb-space-md) var(--mb-space-lg);
          border-bottom: 1px solid var(--divider-color);
        }
        .head-text { display: flex; align-items: baseline; gap: var(--mb-space-md); min-width: 0; }
        header h2 {
          margin: 0; font-size: var(--mb-font-size-lg);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .step-count {
          flex-shrink: 0; color: var(--secondary-text-color);
          font-size: var(--mb-font-size-sm); font-variant-numeric: tabular-nums;
        }
        .close {
          background: transparent; border: none; font-size: 22px; line-height: 1;
          color: var(--secondary-text-color); cursor: pointer;
        }
        .body { padding: var(--mb-space-lg); overflow-y: auto; }
        .resume-banner {
          display: flex; align-items: center; justify-content: space-between;
          gap: var(--mb-space-sm);
          background: var(--secondary-background-color);
          border-radius: var(--mb-radius-sm);
          padding: var(--mb-space-sm) var(--mb-space-md);
          margin-bottom: var(--mb-space-md);
          font-size: var(--mb-font-size-sm);
          color: var(--secondary-text-color);
        }
        ol.steps { list-style: none; margin: 0; padding: 0; }
        .step {
          display: flex; gap: var(--mb-space-md);
          padding: var(--mb-space-sm) 0;
        }
        .step + .step { border-top: 1px solid var(--divider-color); }
        .bullet {
          flex-shrink: 0;
          width: 24px; height: 24px; border-radius: 50%;
          display: inline-flex; align-items: center; justify-content: center;
          font-size: var(--mb-font-size-sm); font-variant-numeric: tabular-nums;
          border: 1px solid var(--divider-color);
          color: var(--secondary-text-color);
          margin-top: 2px;
        }
        .step.active .bullet {
          background: var(--primary-color); border-color: var(--primary-color);
          color: var(--text-primary-color, white);
        }
        .step.done .bullet {
          background: var(--success-color, #4caf50);
          border-color: var(--success-color, #4caf50);
          color: white;
        }
        .step-body { flex: 1; min-width: 0; }
        .step-title { padding-top: 3px; overflow-wrap: anywhere; }
        .step.done .step-title {
          color: var(--secondary-text-color);
          font-size: var(--mb-font-size-sm);
        }
        .step.future .step-title { color: var(--disabled-text-color, var(--secondary-text-color)); opacity: 0.7; }
        .step.active .step-title { font-weight: 600; }
        .step-card { margin-top: var(--mb-space-sm); }
        .note { font-size: var(--mb-font-size-sm); color: var(--secondary-text-color); margin-top: var(--mb-space-xs); }
        .badges { display: flex; flex-wrap: wrap; gap: var(--mb-space-xs); margin: var(--mb-space-xs) 0; }
        .badge {
          font-size: var(--mb-font-size-sm);
          background: var(--secondary-background-color);
          border-radius: 999px;
          padding: 2px 10px;
        }
        .hints {
          background: var(--secondary-background-color);
          border-radius: var(--mb-radius-sm);
          padding: var(--mb-space-sm) var(--mb-space-md);
          margin: var(--mb-space-sm) 0;
          font-size: var(--mb-font-size-sm);
        }
        .hints-title { color: var(--secondary-text-color); margin-bottom: var(--mb-space-xs); }
        .hints ul { margin: 0; padding-left: 1.2em; }
        .prompt-card {
          background: rgba(255, 167, 38, 0.12);
          border: 1px solid var(--warning-color, #ffa726);
          border-radius: var(--mb-radius-sm);
          padding: var(--mb-space-sm) var(--mb-space-md);
          margin: var(--mb-space-sm) 0;
          display: flex; flex-direction: column; gap: var(--mb-space-sm);
          font-size: var(--mb-font-size-sm);
        }
        .muted { color: var(--secondary-text-color); font-size: var(--mb-font-size-sm); }
        .instruction { margin: 0 0 var(--mb-space-md) 0; }
        .finish h3 { margin: 0 0 var(--mb-space-md) 0; }
        .progress-row { display: flex; align-items: center; gap: var(--mb-space-sm); margin-bottom: var(--mb-space-sm); }
        .progress-bar {
          flex: 1; height: 8px; background: var(--secondary-background-color);
          border-radius: 4px; overflow: hidden;
        }
        .progress-fill {
          height: 100%; background: var(--primary-color);
          transition: width 200ms linear;
        }
        .progress-pct { font-variant-numeric: tabular-nums; font-size: var(--mb-font-size-sm); }
        .error {
          background: rgba(244, 67, 54, 0.1); color: var(--error-color);
          padding: var(--mb-space-sm); border-radius: var(--mb-radius-sm);
          margin: var(--mb-space-sm) 0;
        }
        .unsupported-note {
          background: rgba(255, 167, 38, 0.12);
          color: var(--warning-color, #ffa726);
          padding: var(--mb-space-sm) var(--mb-space-md);
          border-radius: var(--mb-radius-sm);
          margin: var(--mb-space-sm) 0;
          font-size: var(--mb-font-size-sm);
          line-height: 1.4;
        }
        .actions {
          display: flex; justify-content: flex-end; align-items: center;
          gap: var(--mb-space-sm);
          margin-top: var(--mb-space-md);
        }
        button {
          padding: var(--mb-space-sm) var(--mb-space-lg);
          border-radius: var(--mb-radius-sm);
          border: 1px solid var(--divider-color);
          background: transparent; color: var(--primary-text-color);
          font-size: var(--mb-font-size-md); cursor: pointer;
        }
        button:hover { background: var(--secondary-background-color); }
        button.primary {
          background: var(--primary-color); border-color: var(--primary-color);
          color: var(--text-primary-color, white);
        }
        button.primary[disabled] { opacity: 0.6; cursor: default; }
        button.ghost { color: var(--secondary-text-color); }
        button.big { min-height: 44px; padding: var(--mb-space-sm) var(--mb-space-xl); }
        .confirm-overlay {
          position: absolute; inset: 0;
          background: rgba(0,0,0,0.45);
          border-radius: var(--mb-radius-md);
          display: flex; align-items: center; justify-content: center;
          padding: var(--mb-space-lg);
        }
        .confirm-box {
          background: var(--card-background-color);
          border-radius: var(--mb-radius-md);
          padding: var(--mb-space-lg);
          max-width: 420px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .confirm-box h3 { margin: 0 0 var(--mb-space-sm) 0; }
        .confirm-box p { margin: 0 0 var(--mb-space-md) 0; font-size: var(--mb-font-size-sm); }
        @media (max-width: 480px) {
          .backdrop { align-items: stretch; justify-content: stretch; }
          .dialog {
            min-width: 100vw; max-width: 100vw; width: 100vw;
            max-height: 100vh; height: 100%;
            border-radius: 0;
          }
          .confirm-overlay { border-radius: 0; }
          button.primary, button.big { min-height: 48px; }
        }
      `,
    ];
  }
}

if (!customElements.get("melitta-brew-wizard")) customElements.define("melitta-brew-wizard", MelittaBrewWizard);
