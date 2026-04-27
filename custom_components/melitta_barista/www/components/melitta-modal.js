/**
 * Reusable modal dialog.
 *
 * Slot-driven content: callers stick form markup inside <melitta-modal>,
 * set `.open=${true}` and `.title=${"…"}`, listen for `close` events.
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaModal extends LitElement {
  static get properties() {
    return {
      open: { type: Boolean, reflect: true },
      title: { type: String },
    };
  }

  constructor() {
    super();
    this.open = false;
    this.title = "";
  }

  _close() {
    this.open = false;
    this.dispatchEvent(new CustomEvent("close", { bubbles: true, composed: true }));
  }

  _onBackdropClick(e) {
    if (e.target === e.currentTarget) this._close();
  }

  connectedCallback() {
    super.connectedCallback();
    this._keyHandler = (e) => { if (e.key === "Escape" && this.open) this._close(); };
    window.addEventListener("keydown", this._keyHandler);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._keyHandler) window.removeEventListener("keydown", this._keyHandler);
  }

  render() {
    if (!this.open) return "";
    return html`
      <div class="backdrop" @click=${(e) => this._onBackdropClick(e)}>
        <div class="dialog" role="dialog" aria-modal="true">
          <header>
            <h3>${this.title}</h3>
            <button class="close" @click=${() => this._close()}>×</button>
          </header>
          <div class="body">
            <slot></slot>
          </div>
        </div>
      </div>
    `;
  }

  static get styles() {
    return css`
      .backdrop {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.55);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 100;
      }
      .dialog {
        background: var(--card-background-color);
        color: var(--primary-text-color);
        border-radius: 8px;
        min-width: 320px;
        max-width: min(720px, 92vw);
        max-height: 86vh;
        display: flex;
        flex-direction: column;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
      }
      header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 16px;
        border-bottom: 1px solid var(--divider-color);
      }
      h3 { margin: 0; font-size: 16px; }
      .close {
        background: transparent;
        border: none;
        font-size: 22px;
        line-height: 1;
        color: var(--secondary-text-color);
        cursor: pointer;
      }
      .close:hover { color: var(--primary-text-color); }
      .body {
        padding: 16px;
        overflow-y: auto;
      }
    `;
  }
}

if (!customElements.get('melitta-modal')) customElements.define('melitta-modal', MelittaModal);
