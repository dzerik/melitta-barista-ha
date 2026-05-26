/**
 * <melitta-sommelier-favorites> — modal listing saved favorites.
 *
 * Public props:
 *   - hass: HomeAssistant
 *   - entryId: string (target machine for brew operations)
 *   - lang: string
 *   - open: boolean
 *
 * Dispatches:
 *   - @close: user closes the modal
 *   - @brew {detail: {recipe, favoriteId}}: user clicks Brew on a favorite —
 *     the parent (melitta-sommelier) opens <melitta-brew-wizard> with
 *     source="favorite", sourceId=favoriteId.
 *
 * Loads favorites on mount + on every open (so external rate/edit changes
 * reflect on reopen).
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";
import { t } from "../i18n.js";
import "./melitta-modal.js";
import "./melitta-confirm.js";
import "./ui/melitta-star-rating.js";

class MelittaSommelierFavorites extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      activeProfile: { type: Number },
      open: { type: Boolean, reflect: true },
      _favorites: { state: true },
      _loading: { state: true },
      _editingId: { state: true },
      _editName: { state: true },
      _editDescription: { state: true },
      _editNote: { state: true },
      _error: { state: true },
    };
  }

  constructor() {
    super();
    this.open = false;
    this._favorites = [];
    this._loading = false;
    this._editingId = null;
    this._editName = "";
    this._editDescription = "";
    this._editNote = "";
    this._error = "";
  }

  _t(key, params) { return t(key, this.lang || "en", params); }

  updated(changed) {
    if (changed.has("open") && this.open) {
      this._loadFavorites();
    }
  }

  async _loadFavorites() {
    if (!this.hass) return;
    this._loading = true;
    this._error = "";
    try {
      const payload = { type: "melitta_barista/sommelier/favorites/list" };
      if (this.activeProfile != null) {
        payload.machine_profile_filter = this.activeProfile;
      }
      const result = await this.hass.callWS(payload);
      this._favorites = result.favorites || [];
    } catch (e) {
      this._error = `${this._t("favorites.load_failed")}: ${e.message || e}`;
    } finally {
      this._loading = false;
    }
  }

  _close() {
    this.dispatchEvent(new CustomEvent("close", { bubbles: true, composed: true }));
  }

  async _onRate(favorite, rating) {
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/recipe/rate",
        target_id: favorite.id,
        target_type: "favorite",
        rating,
      });
      favorite.rating = rating;
      this.requestUpdate();
    } catch (e) {
      this._error = `${this._t("favorites.rate_failed")}: ${e.message || e}`;
    }
  }

  async _onUnrate(favorite) {
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/recipe/unrate",
        target_id: favorite.id,
        target_type: "favorite",
      });
      favorite.rating = null;
      favorite.note = null;
      this.requestUpdate();
    } catch (e) {
      this._error = `${this._t("favorites.rate_failed")}: ${e.message || e}`;
    }
  }

  _onBrew(favorite) {
    this.dispatchEvent(new CustomEvent("brew", {
      bubbles: true, composed: true,
      detail: { recipe: favorite, favoriteId: favorite.id },
    }));
  }

  async _onDelete(favorite) {
    let dialog = this.renderRoot.querySelector("melitta-confirm");
    if (!dialog) {
      dialog = document.createElement("melitta-confirm");
      this.renderRoot.appendChild(dialog);
    }
    const ok = await dialog.ask({
      title: this._t("favorites.delete.title"),
      message: this._t("favorites.delete.confirm", { name: favorite.name }),
      confirmLabel: this._t("common.delete"),
      cancelLabel: this._t("common.cancel"),
      destructive: true,
    });
    if (!ok) return;
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/favorites/remove",
        favorite_id: favorite.id,
      });
      this._favorites = this._favorites.filter((f) => f.id !== favorite.id);
    } catch (e) {
      this._error = `${this._t("favorites.delete_failed")}: ${e.message || e}`;
    }
  }

  _startEdit(favorite) {
    this._editingId = favorite.id;
    this._editName = favorite.name || "";
    this._editDescription = favorite.description || "";
    this._editNote = favorite.note || "";
  }

  _cancelEdit() {
    this._editingId = null;
  }

  async _saveEdit(favorite) {
    const patch = {};
    if (this._editName !== favorite.name) patch.name = this._editName;
    if (this._editDescription !== favorite.description) patch.description = this._editDescription;
    if (this._editNote !== (favorite.note || "")) patch.note = this._editNote;
    if (Object.keys(patch).length === 0) {
      this._editingId = null;
      return;
    }
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/favorites/update",
        favorite_id: favorite.id,
        ...patch,
      });
      Object.assign(favorite, patch);
      this._editingId = null;
    } catch (e) {
      this._error = `${this._t("favorites.update_failed")}: ${e.message || e}`;
    }
  }

  _renderFavorite(favorite) {
    if (this._editingId === favorite.id) {
      return html`
        <div class="card editing">
          <input
            class="edit-name"
            .value=${this._editName}
            @input=${(e) => { this._editName = e.target.value; }}
            placeholder=${this._t("favorites.name_placeholder")}>
          <textarea
            class="edit-desc"
            .value=${this._editDescription}
            @input=${(e) => { this._editDescription = e.target.value; }}
            placeholder=${this._t("favorites.description_placeholder")}></textarea>
          ${favorite.rating ? html`
            <textarea
              class="edit-note"
              .value=${this._editNote}
              @input=${(e) => { this._editNote = e.target.value; }}
              placeholder=${this._t("favorites.note_placeholder")}></textarea>
          ` : html`<p class="muted">${this._t("favorites.note_needs_rating")}</p>`}
          <div class="actions">
            <button class="ghost" @click=${() => this._cancelEdit()}>${this._t("common.cancel")}</button>
            <button class="primary" @click=${() => this._saveEdit(favorite)}>${this._t("common.save")}</button>
          </div>
        </div>
      `;
    }
    return html`
      <div class="card">
        <div class="card-head">
          <h4>${favorite.name}</h4>
          <div class="meta">
            <melitta-star-rating
              .value=${favorite.rating || 0}
              @rate=${(e) => this._onRate(favorite, e.detail.rating)}
              @unrate=${() => this._onUnrate(favorite)}>
            </melitta-star-rating>
          </div>
        </div>
        ${favorite.description ? html`<p class="desc">${favorite.description}</p>` : ""}
        ${favorite.note ? html`<p class="note">${this._t("favorites.note_label")}: <em>${favorite.note}</em></p>` : ""}
        <div class="meta-row">
          ${favorite.brew_count ? html`<span class="badge">${this._t("favorites.brewed_count", { n: favorite.brew_count })}</span>` : ""}
          ${favorite.last_brewed_at ? html`<span class="badge muted">${new Date(favorite.last_brewed_at).toLocaleDateString()}</span>` : ""}
        </div>
        <div class="actions">
          <button class="ghost" @click=${() => this._onDelete(favorite)}>${this._t("common.delete")}</button>
          <button class="ghost" @click=${() => this._startEdit(favorite)}>${this._t("common.edit")}</button>
          <button class="primary" @click=${() => this._onBrew(favorite)}>${this._t("favorites.brew")}</button>
        </div>
      </div>
    `;
  }

  render() {
    if (!this.open) return html``;
    return html`
      <melitta-modal .open=${this.open} .title=${this._t("favorites.modal_title")}
                     @close=${() => this._close()}>
        ${this._error ? html`<div class="error">${this._error}</div>` : ""}
        ${this._loading ? html`<p class="muted">${this._t("common.loading")}</p>` : ""}
        ${!this._loading && this._favorites.length === 0
          ? html`<p class="muted">${this._t("favorites.empty")}</p>`
          : html`<div class="list">${this._favorites.map((f) => this._renderFavorite(f))}</div>`}
      </melitta-modal>
    `;
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        :host { display: contents; }
        .list { display: flex; flex-direction: column; gap: var(--mb-space-md); }
        .card {
          padding: var(--mb-space-md);
          border: 1px solid var(--divider-color);
          border-radius: var(--mb-radius-md);
          background: var(--secondary-background-color);
        }
        .card.editing {
          background: var(--card-background-color);
        }
        .card-head {
          display: flex; align-items: center; justify-content: space-between;
          gap: var(--mb-space-sm);
          margin-bottom: var(--mb-space-sm);
        }
        h4 { margin: 0; font-size: var(--mb-font-size-md); }
        .desc { margin: 0 0 var(--mb-space-sm) 0; color: var(--primary-text-color); }
        .note { margin: 0 0 var(--mb-space-sm) 0; color: var(--secondary-text-color); }
        .meta-row { display: flex; gap: var(--mb-space-xs); margin-bottom: var(--mb-space-sm); flex-wrap: wrap; }
        .badge {
          font-size: var(--mb-font-size-sm);
          padding: 2px var(--mb-space-sm);
          border-radius: 999px;
          background: var(--primary-background-color);
        }
        .badge.muted { color: var(--secondary-text-color); }
        .muted { color: var(--secondary-text-color); }
        .actions {
          display: flex; justify-content: flex-end; gap: var(--mb-space-sm);
        }
        .edit-name, .edit-desc, .edit-note {
          width: 100%;
          padding: var(--mb-space-sm);
          margin-bottom: var(--mb-space-sm);
          border: 1px solid var(--divider-color);
          border-radius: var(--mb-radius-sm);
          background: var(--primary-background-color);
          color: var(--primary-text-color);
          font-family: inherit;
          font-size: var(--mb-font-size-md);
          box-sizing: border-box;
        }
        .edit-desc, .edit-note { min-height: 60px; resize: vertical; }
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
      `,
    ];
  }
}

if (!customElements.get("melitta-sommelier-favorites")) {
  customElements.define("melitta-sommelier-favorites", MelittaSommelierFavorites);
}
