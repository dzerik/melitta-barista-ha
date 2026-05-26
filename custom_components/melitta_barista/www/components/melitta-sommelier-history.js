/**
 * <melitta-sommelier-history> — modal showing past generation sessions.
 *
 * Public props:
 *   - hass: HomeAssistant
 *   - entryId: string
 *   - lang: string
 *   - open: boolean
 *
 * Dispatches:
 *   - @close: user closes the modal.
 *   - @brew {detail: {recipe}}: user clicks Brew on a history recipe — parent
 *     (melitta-sommelier) opens <melitta-brew-wizard> with source="generated".
 *
 * Loads `melitta_barista/sommelier/history/list` on every open. Sessions
 * are expandable; recipes within an expanded session show inline rating +
 * a Brew button. A "Clear history" footer button calls
 * `melitta_barista/sommelier/history/clear` (with keep_favorited=true) after
 * a <melitta-confirm> prompt.
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";
import { t } from "../i18n.js";
import "./melitta-modal.js";
import "./melitta-confirm.js";
import "./ui/melitta-star-rating.js";

const PAGE_SIZE = 20;

class MelittaSommelierHistory extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      activeProfile: { type: Number },
      open: { type: Boolean, reflect: true },
      _sessions: { state: true },
      _loading: { state: true },
      _hasMore: { state: true },
      _expandedSessionId: { state: true },
      _error: { state: true },
      _clearing: { state: true },
    };
  }

  constructor() {
    super();
    this.open = false;
    this._sessions = [];
    this._loading = false;
    this._hasMore = false;
    this._expandedSessionId = null;
    this._error = "";
    this._clearing = false;
  }

  _t(key, params) { return t(key, this.lang || "en", params); }

  updated(changed) {
    if (changed.has("open") && this.open) {
      this._sessions = [];
      this._expandedSessionId = null;
      this._loadPage(0);
    }
  }

  async _loadPage(offset) {
    if (!this.hass) return;
    this._loading = true;
    this._error = "";
    try {
      const payload = {
        type: "melitta_barista/sommelier/history/list",
        limit: PAGE_SIZE,
        offset,
      };
      if (this.activeProfile != null) {
        payload.machine_profile_filter = this.activeProfile;
      }
      const result = await this.hass.callWS(payload);
      const page = result.sessions || [];
      this._sessions = offset === 0 ? page : [...this._sessions, ...page];
      this._hasMore = page.length === PAGE_SIZE;
    } catch (e) {
      this._error = `${this._t("history.load_failed")}: ${e.message || e}`;
    } finally {
      this._loading = false;
    }
  }

  _close() {
    this.dispatchEvent(new CustomEvent("close", { bubbles: true, composed: true }));
  }

  _toggleExpand(sessionId) {
    this._expandedSessionId =
      this._expandedSessionId === sessionId ? null : sessionId;
  }

  _onBrew(recipe) {
    this.dispatchEvent(new CustomEvent("brew", {
      bubbles: true, composed: true,
      detail: { recipe },
    }));
  }

  async _onRateRecipe(recipe, rating) {
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/recipe/rate",
        target_id: recipe.id,
        target_type: "generated",
        rating,
      });
      recipe.rating = rating;
      this.requestUpdate();
    } catch (e) {
      this._error = `${this._t("history.rate_failed")}: ${e.message || e}`;
    }
  }

  async _onUnrateRecipe(recipe) {
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/recipe/unrate",
        target_id: recipe.id,
        target_type: "generated",
      });
      recipe.rating = null;
      recipe.note = null;
      this.requestUpdate();
    } catch (e) {
      this._error = `${this._t("history.rate_failed")}: ${e.message || e}`;
    }
  }

  async _onClearHistory() {
    let dialog = this.renderRoot.querySelector("melitta-confirm");
    if (!dialog) {
      dialog = document.createElement("melitta-confirm");
      this.renderRoot.appendChild(dialog);
    }
    const ok = await dialog.ask({
      title: this._t("history.clear.title"),
      message: this._t("history.clear.confirm"),
      confirmLabel: this._t("history.clear.confirm_button"),
      cancelLabel: this._t("common.cancel"),
      destructive: true,
    });
    if (!ok) return;
    this._clearing = true;
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/sommelier/history/clear",
        keep_favorited: true,
      });
      const cleared = (result && result.cleared) || 0;
      this._loadPage(0);
      this._error = `✓ ${this._t("history.clear.done", { n: cleared })}`;
    } catch (e) {
      this._error = `${this._t("history.clear.failed")}: ${e.message || e}`;
    } finally {
      this._clearing = false;
    }
  }

  _formatDate(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleString(this.lang || "en", {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  _renderRecipe(recipe) {
    return html`
      <div class="recipe">
        <div class="recipe-head">
          <strong>${recipe.name}</strong>
          <melitta-star-rating
            .value=${recipe.rating || 0}
            @rate=${(e) => this._onRateRecipe(recipe, e.detail.rating)}
            @unrate=${() => this._onUnrateRecipe(recipe)}>
          </melitta-star-rating>
        </div>
        ${recipe.description ? html`<p class="desc">${recipe.description}</p>` : ""}
        ${recipe.note ? html`<p class="note">${this._t("history.note_label")}: <em>${recipe.note}</em></p>` : ""}
        <div class="actions">
          ${recipe.brewed ? html`<span class="badge muted">${this._t("history.brewed")}</span>` : ""}
          <button class="primary" @click=${() => this._onBrew(recipe)}>${this._t("history.brew_again")}</button>
        </div>
      </div>
    `;
  }

  _renderSession(session) {
    const expanded = this._expandedSessionId === session.id;
    const recipeCount = (session.recipes || []).length;
    return html`
      <div class="session">
        <button class="session-head" @click=${() => this._toggleExpand(session.id)}>
          <div class="session-date">${this._formatDate(session.created_at)}</div>
          <div class="session-meta">
            <span class="badge">${this._t(`history.mode.${session.mode}`)}</span>
            ${session.occasion ? html`<span class="badge muted">${session.occasion}</span>` : ""}
            <span class="muted">${this._t("history.recipe_count", { n: recipeCount })}</span>
          </div>
          <span class="chevron">${expanded ? "▼" : "▶"}</span>
        </button>
        ${expanded ? html`
          <div class="session-body">
            ${(session.recipes || []).map((r) => this._renderRecipe(r))}
          </div>
        ` : ""}
      </div>
    `;
  }

  render() {
    if (!this.open) return html``;
    return html`
      <melitta-modal .open=${this.open} .title=${this._t("history.modal_title")}
                     @close=${() => this._close()}>
        ${this._error ? html`<div class="error">${this._error}</div>` : ""}
        ${this._loading && this._sessions.length === 0
          ? html`<p class="muted">${this._t("common.loading")}</p>`
          : ""}
        ${!this._loading && this._sessions.length === 0
          ? html`<p class="muted">${this._t("history.empty")}</p>`
          : html`
              <div class="list">${this._sessions.map((s) => this._renderSession(s))}</div>
              ${this._hasMore ? html`
                <div class="actions footer-actions">
                  <button class="ghost" ?disabled=${this._loading}
                          @click=${() => this._loadPage(this._sessions.length)}>
                    ${this._loading ? this._t("common.loading") : this._t("history.load_more")}
                  </button>
                </div>
              ` : ""}
              <div class="actions footer-actions">
                <button class="ghost destructive" ?disabled=${this._clearing}
                        @click=${() => this._onClearHistory()}>
                  ${this._clearing ? this._t("common.loading") : this._t("history.clear.button")}
                </button>
              </div>
            `}
      </melitta-modal>
    `;
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        :host { display: contents; }
        .list { display: flex; flex-direction: column; gap: var(--mb-space-sm); }
        .session {
          border: 1px solid var(--divider-color);
          border-radius: var(--mb-radius-md);
          background: var(--secondary-background-color);
          overflow: hidden;
        }
        .session-head {
          display: flex; align-items: center; gap: var(--mb-space-md);
          width: 100%;
          padding: var(--mb-space-md);
          background: transparent;
          border: none;
          color: var(--primary-text-color);
          cursor: pointer;
          font-family: inherit;
          text-align: left;
        }
        .session-head:hover { background: var(--primary-background-color); }
        .session-date { font-weight: 500; font-size: var(--mb-font-size-md); }
        .session-meta { display: flex; gap: var(--mb-space-xs); align-items: center; flex-wrap: wrap; flex: 1; }
        .chevron { color: var(--secondary-text-color); font-size: var(--mb-font-size-sm); }
        .session-body {
          padding: var(--mb-space-md);
          background: var(--card-background-color);
          display: flex; flex-direction: column; gap: var(--mb-space-sm);
          border-top: 1px solid var(--divider-color);
        }
        .recipe {
          padding: var(--mb-space-sm);
          border: 1px solid var(--divider-color);
          border-radius: var(--mb-radius-sm);
        }
        .recipe-head {
          display: flex; align-items: center; justify-content: space-between;
          gap: var(--mb-space-sm);
          margin-bottom: var(--mb-space-xs);
        }
        .desc { margin: 0 0 var(--mb-space-xs) 0; color: var(--primary-text-color); }
        .note { margin: 0 0 var(--mb-space-xs) 0; color: var(--secondary-text-color); font-size: var(--mb-font-size-sm); }
        .badge {
          font-size: var(--mb-font-size-sm);
          padding: 2px var(--mb-space-sm);
          border-radius: 999px;
          background: var(--primary-background-color);
          color: var(--primary-text-color);
        }
        .badge.muted { color: var(--secondary-text-color); }
        .muted { color: var(--secondary-text-color); font-size: var(--mb-font-size-sm); }
        .actions {
          display: flex; justify-content: flex-end; gap: var(--mb-space-sm);
        }
        .footer-actions { margin-top: var(--mb-space-md); }
        .error {
          background: rgba(244, 67, 54, 0.1); color: var(--error-color);
          padding: var(--mb-space-sm); border-radius: var(--mb-radius-sm);
          margin-bottom: var(--mb-space-md);
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
        button.ghost.destructive {
          color: var(--error-color);
          border-color: var(--error-color);
        }
        button:disabled { opacity: 0.5; cursor: default; }
      `,
    ];
  }
}

if (!customElements.get("melitta-sommelier-history")) {
  customElements.define("melitta-sommelier-history", MelittaSommelierHistory);
}
