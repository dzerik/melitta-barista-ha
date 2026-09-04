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
const PANEL_VERSION = _v || "dev";

await Promise.all([
  import(`./components/melitta-toast.js${_q}`),
  import(`./components/melitta-modal.js${_q}`),
  import(`./components/melitta-confirm.js${_q}`),
  import(`./components/melitta-status.js${_q}`),
  import(`./components/melitta-diagnostics.js${_q}`),
  import(`./components/melitta-recipes.js${_q}`),
  import(`./components/melitta-beans.js${_q}`),
  import(`./components/melitta-additives.js${_q}`),
  import(`./components/melitta-producers.js${_q}`),
  import(`./components/melitta-sommelier.js${_q}`),
  import(`./components/melitta-sommelier-favorites.js${_q}`),
  import(`./components/melitta-sommelier-history.js${_q}`),
  import(`./components/melitta-sommelier-presets.js${_q}`),
  import(`./components/melitta-brew-wizard.js${_q}`),
  import(`./components/ui/melitta-star-rating.js${_q}`),
  import(`./components/ui/melitta-drink-icon.js${_q}`),
  import(`./components/melitta-settings.js${_q}`),
  import(`./components/melitta-system.js${_q}`),
]);

import { LitElement, html, css } from "./lit-base.js";
import { t } from "./i18n/index.js";
import { setServerStrings } from "./i18n/server-strings.js";

const TAB_IDS = [
  "sommelier", "recipes", "beans", "additives", "producers", "system",
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
      _brandTheme: { state: true },
      _contract: { state: true },
      _serverStrings: { state: true },
      _vocab: { state: true },
      _logoFailed: { state: true },
    };
  }

  constructor() {
    super();
    this._tab = TAB_IDS[0];
    this._entries = [];
    this._activeEntry = "";
    this._error = "";
    this._hassReady = false;
    this._brandTheme = null;
    this._contract = null;
    this._serverStrings = null;
    this._vocab = null;
    this._vocabFetched = false;
    this._i18nLocale = null;
    this._logoFailed = false;
  }

  /** Current language code for translations. */
  get _lang() {
    return (this.hass && (this.hass.locale?.language || this.hass.language)) || "en";
  }

  _t(key, params) {
    return t(key, this._lang, params);
  }

  updated(changedProps) {
    if (changedProps.has("hass") && this.hass && !this._hassReady) {
      this._hassReady = true;
      this._loadEntries();
      this._loadVocab();
    }
    // HA locale changes arrive as hass updates — re-fetch server strings
    // for the new locale (no-op while the locale is unchanged).
    if (changedProps.has("hass") && this.hass) {
      this._loadServerStrings();
    }
  }

  /**
   * Fetch machine-domain display strings (UI Contract §6.3) via
   * `melitta_barista/i18n/get`, once per HA locale, alongside the
   * contract fetch. The merged map feeds the pure server-string registry
   * (i18n/server-strings.js) and is passed down to components as the
   * `.serverStrings` prop so they re-render when strings arrive.
   *
   * Failure (an older backend without the command, a transient WS error)
   * degrades to the panel bundles only — never a panel error, and never
   * anything beyond display strings (§6.3.2).
   *
   * The request deliberately omits `domains`: an omitted list means "all
   * domains", so every domain the server grows — `wizard` as the 7th
   * member in 0.94 (§6.3.7), and whatever follows it — arrives without a
   * panel change. Never narrow this to an explicit list; a client that
   * sends one has to remember to add "wizard" and every future domain.
   */
  async _loadServerStrings() {
    const locale = this._lang;
    if (!this.hass || locale === this._i18nLocale || this._i18nInFlight) return;
    // The locale is latched only on success. A dropped frame at panel open
    // used to cost every served string for the whole session, and this wave
    // widened that from a handful of labels to the entire machine domain.
    this._i18nInFlight = true;
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/i18n/get",
        locale,
      });
      const strings =
        result && typeof result.strings === "object" && result.strings !== null
          ? result.strings
          : null;
      setServerStrings(strings);
      this._serverStrings = strings;
      this._i18nLocale = locale;
    } catch (e) {
      // Graceful absence now, retry on the next update: bundle and humanized
      // tiers cover the gap meanwhile.
      setServerStrings(null);
      this._serverStrings = null;
    } finally {
      this._i18nInFlight = false;
    }
  }

  /**
   * Fetch the sommelier enum vocabulary (UI Contract §9.2) via
   * `melitta_barista/vocab/get` — machine-independent constant data, so
   * one fetch per panel session, no entry_id. The served families feed
   * the sommelier/beans/additives pickers as the `.vocab` prop.
   *
   * Failure (an older backend without the command, a transient WS
   * error) degrades per feature to the components' hardcoded fallback
   * arrays (§9.2.6.1) — never a panel error.
   */
  async _loadVocab() {
    if (!this.hass || this._vocabFetched) return;
    this._vocabFetched = true;
    try {
      const result = await this.hass.callWS({
        type: "melitta_barista/vocab/get",
      });
      this._vocab =
        result && typeof result.vocab === "object" && result.vocab !== null
          ? result.vocab
          : null;
    } catch (e) {
      // Graceful absence: hardcoded fallback arrays for the session.
      this._vocab = null;
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
      this._loadBrandTheme();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  /**
   * Fetch the UI Contract document (docs/UI_CONTRACT.md §3.3) for the
   * active entry — one fetch feeds both the brand badge (§3.10) and the
   * capability/vocabulary consumers (recipes editor gating, form option
   * lists, portion clamps).
   *
   * Best-effort: an older backend without `ui_contract/get` (or a
   * machine that has not completed a handshake yet) simply means no
   * badge and fallback vocabularies — never a panel error. Colors and
   * logo_url are validated before rendering; everything is advisory
   * data, not markup.
   */
  async _loadBrandTheme() {
    this._brandTheme = null;
    this._contract = null;
    this._logoFailed = false;
    if (!this._activeEntry || !this.hass) return;
    this._loadServerStrings();
    try {
      const contract = await this.hass.callWS({
        type: "melitta_barista/ui_contract/get",
        entry_id: this._activeEntry,
      });
      this._contract =
        contract && typeof contract === "object" ? contract : null;
      const bt = contract && contract.brand_theme;
      this._brandTheme =
        bt && typeof bt === "object" && typeof bt.wordmark === "string"
          ? bt
          : null;
    } catch (e) {
      // Old backend / contract not ready → graceful absence (§3.10).
      this._brandTheme = null;
      this._contract = null;
    }
    this._ensureVisibleTab();
  }

  /**
   * Capability gate for the DirectKey recipe editor tab.
   *
   * With a loaded contract, `capabilities.supports_recipe_writes` is the
   * authority (Nivona machines hide the tab). Before the contract is
   * available (pre-handshake, older backend) fall back to the entry's
   * brand — DirectKey recipe writes are a Melitta surface.
   */
  _supportsRecipeEditor() {
    const caps = this._contract && this._contract.capabilities;
    if (caps && typeof caps === "object") {
      return caps.supports_recipe_writes !== false;
    }
    const entry = this._entries.find((e) => e.entry_id === this._activeEntry);
    return (entry?.brand || "melitta") === "melitta";
  }

  /** Tab ids visible for the active machine (capability gating). */
  _visibleTabs() {
    return TAB_IDS.filter(
      (id) => id !== "recipes" || this._supportsRecipeEditor(),
    );
  }

  /** If gating hid the active tab, fall back to the first visible one. */
  _ensureVisibleTab() {
    const visible = this._visibleTabs();
    if (!visible.includes(this._tab)) this._tab = visible[0];
  }

  /** Strict #rrggbb validation — brand colors are escaped data (§3.10). */
  _safeColor(value) {
    return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value)
      ? value
      : "";
  }

  _renderBrandBadge() {
    const bt = this._brandTheme;
    if (!bt) return "";
    const accent = this._safeColor(bt.accent);
    const soft = this._safeColor(bt.accent_soft);
    const style = accent && soft ? `background:${soft};color:${accent};` : "";
    const logoUrl =
      typeof bt.logo_url === "string" && bt.logo_url.startsWith("/local/")
        ? bt.logo_url
        : null;
    if (logoUrl && !this._logoFailed) {
      return html`
        <span class="brand-badge" style=${style}>
          <img class="brand-logo" src=${logoUrl} alt=${bt.wordmark}
            @error=${() => { this._logoFailed = true; }}>
        </span>
      `;
    }
    return html`<span class="brand-badge wordmark" style=${style}>${bt.wordmark}</span>`;
  }

  _renderHeader() {
    return html`
      <header>
        <div class="title">
          <ha-icon icon="mdi:coffee-maker"></ha-icon>
          <span>${this._t("panel.title")}</span>
          <span class="version" title="Integration version">v${PANEL_VERSION}</span>
          ${this._renderBrandBadge()}
        </div>
        ${this._entries.length > 1 ? html`
          <select
            class="entry-picker"
            .value=${this._activeEntry}
            @change=${(e) => { this._activeEntry = e.target.value; this._loadBrandTheme(); }}
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
        ${this._visibleTabs().map((id) => html`
          <button
            class=${this._tab === id ? "active" : ""}
            @click=${() => { this._tab = id; }}
          >${this._t(`tabs.${id}`)}</button>
        `)}
      </nav>
    `;
  }

  _renderActiveTab() {
    if (!this._activeEntry) {
      return html`<div class="empty">${this._t("panel.no_entries")}</div>`;
    }
    const props = { hass: this.hass, entryId: this._activeEntry, lang: this._lang };
    switch (this._tab) {
      case "sommelier":
        return html`<melitta-sommelier .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang} .vocab=${this._vocab} .serverStrings=${this._serverStrings}></melitta-sommelier>`;
      case "recipes":
        return html`<melitta-recipes .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang} .contract=${this._contract} .serverStrings=${this._serverStrings}></melitta-recipes>`;
      case "beans":
        return html`<melitta-beans .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang} .vocab=${this._vocab} .serverStrings=${this._serverStrings}></melitta-beans>`;
      case "additives":
        return html`<melitta-additives .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang} .vocab=${this._vocab} .serverStrings=${this._serverStrings}></melitta-additives>`;
      case "producers":
        return html`<melitta-producers .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang}></melitta-producers>`;
      case "system":
        return html`<melitta-system .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang} .contract=${this._contract} .serverStrings=${this._serverStrings}></melitta-system>`;
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
      .version {
        font-size: 11px;
        font-weight: 400;
        padding: 2px 8px;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.12);
        color: var(--secondary-text-color, rgba(255, 255, 255, 0.7));
        font-variant-numeric: tabular-nums;
        margin-left: 4px;
      }
      .brand-badge {
        display: inline-flex;
        align-items: center;
        margin-left: 8px;
        padding: 2px 10px;
        border-radius: 10px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        background: var(--secondary-background-color);
        color: var(--secondary-text-color);
      }
      .brand-logo {
        display: block;
        height: 16px;
        max-width: 72px;
        object-fit: contain;
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

if (!customElements.get('melitta-panel')) customElements.define('melitta-panel', MelittaPanel);
