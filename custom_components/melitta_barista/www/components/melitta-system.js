/**
 * `<melitta-system>` — single top-level tab containing all
 * machine-side / system-level subviews: Status, Settings,
 * Diagnostics, Machine recipes (the existing DirectKey editor —
 * still "coming soon", but moved out of the top nav).
 *
 * Renders an inner tab strip and forwards .hass / .entryId / .lang
 * to the matching child component.
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";
import { t } from "../i18n/index.js";

const SUBTABS = ["status", "settings", "diagnostics", "recipes"];

class MelittaSystem extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _sub: { state: true },
    };
  }

  constructor() {
    super();
    this._sub = SUBTABS[0];
  }

  _t(key, params) { return t(key, this.lang || "en", params); }

  _renderSubtabs() {
    return html`
      <nav class="subtabs">
        ${SUBTABS.map((id) => html`
          <button
            class=${this._sub === id ? "active" : ""}
            @click=${() => { this._sub = id; }}
          >${this._t(`system.subtabs.${id}`)}</button>
        `)}
      </nav>
    `;
  }

  _renderActive() {
    const props = { hass: this.hass, entryId: this.entryId, lang: this.lang };
    switch (this._sub) {
      case "status":
        return html`<melitta-status .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang}></melitta-status>`;
      case "settings":
        return html`<melitta-settings .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang}></melitta-settings>`;
      case "diagnostics":
        return html`<melitta-diagnostics .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang}></melitta-diagnostics>`;
      case "recipes":
        return html`<melitta-recipes .hass=${props.hass} .entryId=${props.entryId} .lang=${props.lang}></melitta-recipes>`;
      default:
        return "";
    }
  }

  render() {
    return html`
      ${this._renderSubtabs()}
      <div class="content">${this._renderActive()}</div>
    `;
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        :host { display: block; }
        nav.subtabs {
          display: flex;
          gap: var(--mb-space-xs);
          margin-bottom: var(--mb-space-lg);
          border-bottom: 1px solid var(--divider-color);
        }
        nav.subtabs button {
          background: transparent;
          border: 1px solid transparent;
          border-bottom: none;
          border-radius: var(--mb-radius-sm) var(--mb-radius-sm) 0 0;
          padding: var(--mb-space-sm) var(--mb-space-md);
          color: var(--secondary-text-color);
          cursor: pointer;
          font-size: var(--mb-font-size-md);
        }
        nav.subtabs button:hover { background: var(--secondary-background-color); }
        nav.subtabs button.active {
          color: var(--primary-color);
          border-color: var(--divider-color);
          border-bottom-color: var(--card-background-color);
          background: var(--card-background-color);
          margin-bottom: -1px;
        }
      `,
    ];
  }
}

if (!customElements.get("melitta-system")) customElements.define("melitta-system", MelittaSystem);
