/**
 * Additives manager tab — STUB.
 *
 * Will manage: syrups, toppings, milk types — all referenced by the Sommelier
 * when it suggests recipes that include "vanilla syrup", "oat milk", etc.
 *
 * Backed by WS handlers `melitta_barista/syrups`, `.../toppings`, `.../milk`
 * (the milk store already partially exists in sommelier_db.py — needs to be
 * extended and unified).
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaAdditives extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
    };
  }

  render() {
    return html`
      <section>
        <h2>Сиропы, топинги, молоко</h2>
        <p class="hint">
          Справочники добавок, которые Сомелье использует при подборе рецептов.
          Заглушка.
        </p>
      </section>
    `;
  }

  static get styles() {
    return css`
      section {
        background: var(--card-background-color);
        border-radius: 8px;
        padding: 16px 20px;
        box-shadow: var(--ha-card-box-shadow);
      }
      h2 { margin: 0 0 12px; font-size: 18px; }
      .hint { color: var(--secondary-text-color); font-size: 14px; line-height: 1.5; margin: 0; }
    `;
  }
}

customElements.define("melitta-additives", MelittaAdditives);
