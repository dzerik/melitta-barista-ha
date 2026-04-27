/**
 * Melitta Barista — admin panel for the Home Assistant sidebar.
 *
 * Top-level coordinator: holds the active tab, fetches per-tab data via the
 * integration's WebSocket API, and delegates rendering to small components in
 * ./components/. Bundled lit 3.x is loaded from ./vendor/lit.js so the panel
 * works on a fresh HA install without HACS-card side effects.
 */

const _v = new URL(import.meta.url).searchParams.get("v") || "";
const _q = _v ? `?v=${_v}` : "";

await Promise.all([
  import(`./components/melitta-toast.js${_q}`),
  import(`./components/melitta-status.js${_q}`),
  import(`./components/melitta-diagnostics.js${_q}`),
  import(`./components/melitta-recipes.js${_q}`),
  import(`./components/melitta-beans.js${_q}`),
  import(`./components/melitta-additives.js${_q}`),
  import(`./components/melitta-sommelier.js${_q}`),
]);

import { LitElement, html, css } from "./lit-base.js";

const TABS = [
  { id: "status",      label: "Состояние",   tag: "melitta-status" },
  { id: "diagnostics", label: "Диагностика", tag: "melitta-diagnostics" },
  { id: "recipes",     label: "Рецепты",     tag: "melitta-recipes" },
  { id: "beans",       label: "Зёрна",       tag: "melitta-beans" },
  { id: "additives",   label: "Добавки",     tag: "melitta-additives" },
  { id: "sommelier",   label: "Сомелье",     tag: "melitta-sommelier" },
];

class MelittaPanel extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean },
      panel: { type: Object },
      _tab: { type: String },
      _entries: { type: Array },
      _activeEntry: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._tab = TABS[0].id;
    this._entries = [];
    this._activeEntry = "";
    this._error = "";
    this._hassReady = false;
  }

  updated(changedProps) {
    if (changedProps.has("hass") && this.hass && !this._hassReady) {
      this._hassReady = true;
      this._loadEntries();
    }
  }

  async _loadEntries() {
    try {
      const result = await this.hass.callWS({ type: "melitta_barista/entries" });
      this._entries = result.entries || [];
      if (this._entries.length && !this._activeEntry) {
        this._activeEntry = this._entries[0].entry_id;
      }
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _renderHeader() {
    return html`
      <header>
        <div class="title">
          <ha-icon icon="mdi:coffee-maker"></ha-icon>
          <span>Melitta Barista</span>
        </div>
        ${this._entries.length > 1 ? html`
          <select
            class="entry-picker"
            .value=${this._activeEntry}
            @change=${(e) => { this._activeEntry = e.target.value; }}
          >
            ${this._entries.map((entry) => html`
              <option value=${entry.entry_id}>${entry.title}</option>
            `)}
          </select>
        ` : ""}
      </header>
    `;
  }

  _renderTabs() {
    return html`
      <nav>
        ${TABS.map((tab) => html`
          <button
            class=${this._tab === tab.id ? "active" : ""}
            @click=${() => { this._tab = tab.id; }}
          >${tab.label}</button>
        `)}
      </nav>
    `;
  }

  _renderActiveTab() {
    if (!this._activeEntry) {
      return html`<div class="empty">Не найдено ни одной интегрированной кофемашины.</div>`;
    }
    const props = { hass: this.hass, entryId: this._activeEntry };
    switch (this._tab) {
      case "status":
        return html`<melitta-status .hass=${props.hass} .entryId=${props.entryId}></melitta-status>`;
      case "diagnostics":
        return html`<melitta-diagnostics .hass=${props.hass} .entryId=${props.entryId}></melitta-diagnostics>`;
      case "recipes":
        return html`<melitta-recipes .hass=${props.hass} .entryId=${props.entryId}></melitta-recipes>`;
      case "beans":
        return html`<melitta-beans .hass=${props.hass} .entryId=${props.entryId}></melitta-beans>`;
      case "additives":
        return html`<melitta-additives .hass=${props.hass} .entryId=${props.entryId}></melitta-additives>`;
      case "sommelier":
        return html`<melitta-sommelier .hass=${props.hass} .entryId=${props.entryId}></melitta-sommelier>`;
      default:
        return "";
    }
  }

  render() {
    return html`
      ${this._renderHeader()}
      ${this._renderTabs()}
      ${this._error ? html`<div class="error">${this._error}</div>` : ""}
      <main>${this._renderActiveTab()}</main>
      <melitta-toast id="toast"></melitta-toast>
    `;
  }

  static get styles() {
    return css`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family);
      }
      header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 20px;
        background: var(--app-header-background-color);
        color: var(--app-header-text-color);
        border-bottom: 1px solid var(--divider-color);
      }
      .title {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 18px;
        font-weight: 500;
      }
      .entry-picker {
        background: transparent;
        color: inherit;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        padding: 4px 8px;
      }
      nav {
        display: flex;
        gap: 4px;
        padding: 8px 16px;
        background: var(--card-background-color);
        border-bottom: 1px solid var(--divider-color);
        overflow-x: auto;
      }
      nav button {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 8px 14px;
        color: var(--secondary-text-color);
        cursor: pointer;
        white-space: nowrap;
        font-size: 14px;
      }
      nav button:hover {
        background: var(--secondary-background-color);
      }
      nav button.active {
        color: var(--primary-color);
        background: var(--secondary-background-color);
        border-color: var(--primary-color);
      }
      main {
        padding: 16px 20px;
      }
      .error {
        margin: 12px 20px;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }
      .empty {
        padding: 48px;
        text-align: center;
        color: var(--secondary-text-color);
      }
    `;
  }
}

customElements.define("melitta-panel", MelittaPanel);
