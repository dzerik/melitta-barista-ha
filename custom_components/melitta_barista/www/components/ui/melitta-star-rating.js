/**
 * <melitta-star-rating> — interactive 5-star rating widget.
 *
 * Usage:
 *   <melitta-star-rating
 *     .value=${4}
 *     ?readonly=${false}
 *     @rate=${(e) => doRate(e.detail.rating)}
 *     @unrate=${() => doUnrate()}>
 *   </melitta-star-rating>
 *
 * - .value: 0..5 (0 means no rating set, 1..5 displays filled stars).
 * - ?readonly: when set, clicks are no-ops (display only).
 * - @rate: dispatched on click of a star (when not already that value).
 * - @unrate: dispatched on click of the currently-active star (toggle off).
 */

import { LitElement, html, css } from "../../lit-base.js";
import { sharedStyles } from "../../design-tokens.js";

class MelittaStarRating extends LitElement {
  static get properties() {
    return {
      value: { type: Number },
      readonly: { type: Boolean, reflect: true },
      _hover: { state: true },
    };
  }

  constructor() {
    super();
    this.value = 0;
    this.readonly = false;
    this._hover = 0;
  }

  _onClick(star) {
    if (this.readonly) return;
    if (star === this.value) {
      this.dispatchEvent(new CustomEvent("unrate", { bubbles: true, composed: true }));
    } else {
      this.dispatchEvent(new CustomEvent("rate", {
        bubbles: true, composed: true,
        detail: { rating: star },
      }));
    }
  }

  _onHover(star) {
    if (this.readonly) return;
    this._hover = star;
  }

  _onLeave() {
    this._hover = 0;
  }

  render() {
    const shown = this._hover || this.value || 0;
    const stars = [1, 2, 3, 4, 5].map((i) => {
      const filled = i <= shown;
      return html`
        <button
          type="button"
          class=${filled ? "star filled" : "star"}
          ?disabled=${this.readonly}
          @click=${() => this._onClick(i)}
          @mouseenter=${() => this._onHover(i)}
          @mouseleave=${() => this._onLeave()}
          aria-label="${i} ${i === 1 ? "star" : "stars"}">
          ${filled ? "★" : "☆"}
        </button>
      `;
    });
    return html`<div class="row" @mouseleave=${() => this._onLeave()}>${stars}</div>`;
  }

  static get styles() {
    return [
      sharedStyles,
      css`
        :host { display: inline-flex; }
        .row { display: inline-flex; gap: 2px; }
        .star {
          background: transparent;
          border: none;
          padding: 0 2px;
          font-size: var(--mb-font-size-lg);
          line-height: 1;
          color: var(--secondary-text-color);
          cursor: pointer;
          font-variant: normal;
        }
        .star.filled {
          color: var(--warning-color, #ffb300);
        }
        .star:disabled {
          cursor: default;
          opacity: 0.85;
        }
        .star:not(:disabled):hover { transform: scale(1.05); }
      `,
    ];
  }
}

if (!customElements.get("melitta-star-rating")) customElements.define("melitta-star-rating", MelittaStarRating);
