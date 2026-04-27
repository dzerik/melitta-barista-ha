/**
 * Toast notifier — call from any tab via window.dispatchEvent or by querying
 * the host panel's #toast element directly.
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaToast extends LitElement {
  static get properties() {
    return {
      _message: { type: String },
      _kind: { type: String },
    };
  }

  constructor() {
    super();
    this._message = "";
    this._kind = "info";
  }

  show(message, kind = "info") {
    this._message = message;
    this._kind = kind;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => { this._message = ""; }, 3500);
  }

  render() {
    if (!this._message) return "";
    return html`<div class="toast ${this._kind}">${this._message}</div>`;
  }

  static get styles() {
    return css`
      :host {
        position: fixed;
        bottom: 24px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 1000;
        pointer-events: none;
      }
      .toast {
        padding: 10px 18px;
        border-radius: 4px;
        background: var(--primary-color);
        color: var(--text-primary-color);
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
        font-size: 14px;
        animation: slide-in 0.2s ease-out;
      }
      .toast.error { background: var(--error-color); }
      .toast.success { background: var(--success-color, #4caf50); }
      @keyframes slide-in {
        from { transform: translateY(20px); opacity: 0; }
        to { transform: translateY(0); opacity: 1; }
      }
    `;
  }
}

if (!customElements.get('melitta-toast')) customElements.define('melitta-toast', MelittaToast);
