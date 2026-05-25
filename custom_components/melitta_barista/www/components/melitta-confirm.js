/**
 * Promise-based confirmation dialog. Replaces native window.confirm().
 *
 * Usage:
 *   const confirm = document.createElement("melitta-confirm");
 *   document.body.appendChild(confirm);
 *   const ok = await confirm.ask({
 *     title: "Delete?",
 *     message: "This cannot be undone.",
 *     confirmLabel: "Delete",
 *     cancelLabel: "Cancel",
 *     destructive: true,
 *   });
 *   confirm.remove();
 *   if (!ok) return;
 *
 * Why a separate component instead of inline modal: keeps call sites
 * declarative (single await), prevents leaking modal state into the
 * caller component, and lets us style "destructive" actions uniformly.
 */

import { LitElement, html, css } from "../lit-base.js";
import { sharedStyles } from "../design-tokens.js";

class MelittaConfirm extends LitElement {
  static get properties() {
    return {
      _open: { state: true },
      _title: { state: true },
      _message: { state: true },
      _confirmLabel: { state: true },
      _cancelLabel: { state: true },
      _destructive: { state: true },
    };
  }

  constructor() {
    super();
    this._open = false;
    this._title = "";
    this._message = "";
    this._confirmLabel = "OK";
    this._cancelLabel = "Cancel";
    this._destructive = false;
    this._resolve = null;
  }

  /**
   * Open the dialog and return a promise that resolves to true (confirmed)
   * or false (cancelled / closed). Only one ask() is active at a time.
   */
  ask({ title, message, confirmLabel, cancelLabel, destructive }) {
    // If a previous ask() is still pending, reject it with false so its
    // caller's await unblocks (otherwise the old promise would leak).
    if (this._resolve) {
      const prev = this._resolve;
      this._resolve = null;
      prev(false);
    }
    this._title = title || "";
    this._message = message || "";
    this._confirmLabel = confirmLabel || "OK";
    this._cancelLabel = cancelLabel || "Cancel";
    this._destructive = Boolean(destructive);
    this._open = true;
    return new Promise((resolve) => { this._resolve = resolve; });
  }

  _resolveWith(value) {
    this._open = false;
    if (this._resolve) {
      const r = this._resolve;
      this._resolve = null;
      r(value);
    }
  }

  render() {
    return html`
      <melitta-modal .open=${this._open} .title=${this._title} @close=${() => this._resolveWith(false)}>
        <p class="message">${this._message}</p>
        <div class="actions">
          <button class="cancel" @click=${() => this._resolveWith(false)}>${this._cancelLabel}</button>
          <button class=${this._destructive ? "confirm destructive" : "confirm"} @click=${() => this._resolveWith(true)}>${this._confirmLabel}</button>
        </div>
      </melitta-modal>
    `;
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        .message {
          margin: 0 0 var(--mb-space-lg) 0;
          color: var(--primary-text-color);
        }
        .actions {
          display: flex;
          justify-content: flex-end;
          gap: var(--mb-space-sm);
        }
        button {
          padding: var(--mb-space-sm) var(--mb-space-lg);
          border-radius: var(--mb-radius-sm);
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          font-size: var(--mb-font-size-md);
          cursor: pointer;
        }
        button:hover { background: var(--secondary-background-color); }
        button.confirm {
          background: var(--primary-color);
          border-color: var(--primary-color);
          color: var(--text-primary-color, white);
        }
        button.confirm.destructive {
          background: var(--error-color);
          border-color: var(--error-color);
        }
      `,
    ];
  }
}

if (!customElements.get("melitta-confirm")) customElements.define("melitta-confirm", MelittaConfirm);
