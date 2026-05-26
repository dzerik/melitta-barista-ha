/**
 * <melitta-brew-wizard> — pre/during/post wizard for brewing a sommelier recipe.
 *
 * Opens on "Brew this" click in melitta-sommelier. Walks the user through:
 *  - pre: manual preparation steps (cup choice, additives, ice).
 *  - during: triggers BLE brew, shows progress bar driven by an estimated
 *    duration; polls melitta_barista/status every 2s to detect the
 *    PRODUCT→READY transition (machine done). Manual "I'm done" button
 *    appears after estimated + 30s if the machine event never arrives
 *    (offline mode, machine outside BLE range, polling failure).
 *  - post: manual finalization (toppings, decoration, instruction).
 *
 * Multi-phase recipes (machine_phases.length === 2) still fire a single
 * brew_freestyle call — the machine sequences both phases internally.
 * Per-phase pauses with user-action UI between phases are a P2c follow-up.
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";
import { t } from "../i18n/index.js";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_BUFFER_S = 30;
const PROGRESS_CAP_PERCENT = 95;

/**
 * Estimated brew duration in seconds for a recipe.
 * Heuristic: warmup (8s) + sum over phases of (portion_ml / 50 * 5s).
 */
function estimateBrewSeconds(recipe) {
  const phases = recipe?.machine_phases || [];
  const pump = phases.reduce((acc, p) => {
    const ml = Number(p?.component?.portion_ml) || 0;
    return acc + (ml / 50) * 5;
  }, 0);
  const warmup = 8;
  return Math.max(15, Math.round(warmup + pump));
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
      _phase: { state: true },
      _brewing: { state: true },
      _brewProgress: { state: true },
      _estimatedSeconds: { state: true },
      _brewStartedAt: { state: true },
      _manualFinishVisible: { state: true },
      _error: { state: true },
    };
  }

  constructor() {
    super();
    this.recipe = null;
    this.open = false;
    this.source = "generated";
    this.sourceId = null;
    // Optimistic default — children stay brand-agnostic and rely on the
    // parent to override when capabilities say otherwise.
    this.canBrew = true;
    this._phase = "pre";
    this._brewing = false;
    this._brewProgress = 0;
    this._estimatedSeconds = 0;
    this._brewStartedAt = 0;
    this._manualFinishVisible = false;
    this._error = "";
    this._pollHandle = null;
    this._progressHandle = null;
  }

  _t(key, params) { return t(key, this.lang || "en", params); }

  _close() {
    this._stopPolling();
    this.open = false;
    this.dispatchEvent(new CustomEvent("close", { bubbles: true, composed: true }));
  }

  _stepsForPhase(phase) {
    const steps = this.recipe?.steps || [];
    return steps.filter((s) => (s.phase || "during") === phase).sort(
      (a, b) => (a.order || 0) - (b.order || 0)
    );
  }

  _renderStep(step) {
    const qty = (step.amount && step.unit) ? ` (${step.amount} ${step.unit})` : "";
    return html`
      <li>
        <strong>${step.action}</strong>${qty}
        ${step.ingredient ? html` — ${step.ingredient}` : ""}
        ${step.notes ? html`<div class="note">${step.notes}</div>` : ""}
      </li>
    `;
  }

  _renderPre() {
    const steps = this._stepsForPhase("pre");
    const cupType = this.recipe?.cup_type;
    return html`
      <h3>${this._t("wizard.pre.title")}</h3>
      ${cupType ? html`<p class="cup">${this._t("wizard.pre.cup")}: <strong>${cupType}</strong></p>` : ""}
      ${steps.length ? html`<ol>${steps.map((s) => this._renderStep(s))}</ol>` :
        html`<p class="muted">${this._t("wizard.pre.no_steps")}</p>`}
      ${!this.canBrew ? html`
        <div class="unsupported-note">${this._t("brewing.unsupported_note")}</div>
      ` : ""}
      <div class="actions">
        <button class="ghost" @click=${() => this._close()}>${this._t("common.cancel")}</button>
        <button class="primary"
                ?disabled=${!this.canBrew}
                title=${!this.canBrew ? this._t("brewing.unsupported_tooltip") : ""}
                @click=${() => this._startBrew()}>${this._t("wizard.pre.start_brew")}</button>
      </div>
    `;
  }

  _renderDuring() {
    const machineSteps = this._stepsForPhase("during");
    return html`
      <h3>${this._t("wizard.during.title")}</h3>
      <div class="progress-row">
        <div class="progress-bar"><div class="progress-fill" style="width: ${this._brewProgress}%"></div></div>
        <span class="progress-pct">${Math.round(this._brewProgress)}%</span>
      </div>
      <p class="muted">${this._t("wizard.during.estimated", { sec: this._estimatedSeconds })}</p>
      ${machineSteps.length ? html`<ol>${machineSteps.map((s) => this._renderStep(s))}</ol>` : ""}
      ${this._error ? html`<div class="error">${this._error}</div>` : ""}
      <div class="actions">
        ${this._manualFinishVisible || this._error
          ? html`<button class="primary" @click=${() => this._advancePost()}>${this._t("wizard.during.im_done")}</button>`
          : html`<span class="muted">${this._t("wizard.during.wait")}</span>`}
      </div>
    `;
  }

  _renderPost() {
    const steps = this._stepsForPhase("post");
    const instruction = this.recipe?.extras?.instruction;
    return html`
      <h3>${this._t("wizard.post.title")}</h3>
      ${steps.length ? html`<ol>${steps.map((s) => this._renderStep(s))}</ol>` :
        html`<p class="muted">${this._t("wizard.post.no_steps")}</p>`}
      ${instruction ? html`<p class="instruction">${instruction}</p>` : ""}
      <div class="actions">
        <button class="primary" @click=${() => this._close()}>${this._t("wizard.post.finish")}</button>
      </div>
    `;
  }

  render() {
    if (!this.open || !this.recipe) return html``;
    return html`
      <div class="backdrop" @click=${(e) => { if (e.target === e.currentTarget) this._close(); }}>
        <div class="dialog" role="dialog" aria-modal="true">
          <header>
            <h2>${this.recipe?.name || this._t("wizard.title")}</h2>
            <button class="close" @click=${() => this._close()}>×</button>
          </header>
          <div class="body">
            ${this._phase === "pre" ? this._renderPre() : ""}
            ${this._phase === "during" ? this._renderDuring() : ""}
            ${this._phase === "post" ? this._renderPost() : ""}
          </div>
        </div>
      </div>
    `;
  }

  async _startBrew() {
    // Defensive guard — the disabled button shouldn't fire, but keep the
    // contract honest if something calls this path programmatically.
    if (!this.canBrew) {
      // eslint-disable-next-line no-console
      console.warn("[melitta-brew-wizard] start brew blocked: canBrew=false");
      this._error = this._t("brewing.unsupported_error");
      return;
    }
    this._phase = "during";
    this._brewing = true;
    this._brewProgress = 0;
    this._error = "";
    this._estimatedSeconds = estimateBrewSeconds(this.recipe);
    this._brewStartedAt = Date.now();
    this._manualFinishVisible = false;

    try {
      const brewCall = this.source === "favorite"
        ? { type: "melitta_barista/sommelier/favorites/brew", favorite_id: this.sourceId || this.recipe.id }
        : { type: "melitta_barista/sommelier/brew", recipe_id: this.sourceId || this.recipe.id };
      await this.hass.callWS(brewCall);
    } catch (e) {
      this._error = `${this._t("wizard.during.brew_failed")}: ${e.message || e}`;
      this._manualFinishVisible = true;
      return;
    }

    this._progressHandle = setInterval(() => {
      const elapsed = (Date.now() - this._brewStartedAt) / 1000;
      const pct = Math.min(PROGRESS_CAP_PERCENT, (elapsed / this._estimatedSeconds) * 100);
      this._brewProgress = pct;
      if (elapsed > this._estimatedSeconds + POLL_TIMEOUT_BUFFER_S) {
        this._manualFinishVisible = true;
      }
    }, 250);

    this._pollHandle = setInterval(() => this._pollStatus(), POLL_INTERVAL_MS);
  }

  async _pollStatus() {
    try {
      const status = await this.hass.callWS({
        type: "melitta_barista/status",
        entry_id: this.entryId,
      });
      if (status && status.is_brewing === false) {
        this._brewProgress = 100;
        this._advancePost();
      }
    } catch (e) {
      this._manualFinishVisible = true;
    }
  }

  _stopPolling() {
    if (this._pollHandle) { clearInterval(this._pollHandle); this._pollHandle = null; }
    if (this._progressHandle) { clearInterval(this._progressHandle); this._progressHandle = null; }
    this._brewing = false;
  }

  _advancePost() {
    this._stopPolling();
    this._phase = "post";
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
          background: var(--card-background-color);
          color: var(--primary-text-color);
          border-radius: var(--mb-radius-md);
          min-width: 360px; max-width: min(640px, 92vw);
          max-height: 86vh;
          display: flex; flex-direction: column;
          box-shadow: 0 12px 32px rgba(0,0,0,0.4);
        }
        header {
          display: flex; align-items: center; justify-content: space-between;
          padding: var(--mb-space-md) var(--mb-space-lg);
          border-bottom: 1px solid var(--divider-color);
        }
        header h2 { margin: 0; font-size: var(--mb-font-size-lg); }
        .close {
          background: transparent; border: none; font-size: 22px; line-height: 1;
          color: var(--secondary-text-color); cursor: pointer;
        }
        .body { padding: var(--mb-space-lg); overflow-y: auto; }
        h3 { margin: 0 0 var(--mb-space-md) 0; font-size: var(--mb-font-size-md); }
        ol { padding-left: 1.2em; margin: 0 0 var(--mb-space-md) 0; }
        li { margin-bottom: var(--mb-space-xs); }
        .note { font-size: var(--mb-font-size-sm); color: var(--secondary-text-color); }
        .cup, .instruction { margin: 0 0 var(--mb-space-md) 0; }
        .muted { color: var(--secondary-text-color); font-size: var(--mb-font-size-sm); }
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
          display: flex; justify-content: flex-end; gap: var(--mb-space-sm);
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
        button.ghost { color: var(--secondary-text-color); }
      `,
    ];
  }
}

if (!customElements.get("melitta-brew-wizard")) customElements.define("melitta-brew-wizard", MelittaBrewWizard);
