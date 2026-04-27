/**
 * Coffee producers & beans manager tab — STUB.
 *
 * Will manage: producers (Lavazza, Illy, ...), beans per producer (with roast
 * level, origin, varietal, notes, recommended brewing). LLM auto-fill: query
 * the HA conversation agent for "describe beans X by producer Y" → structured
 * response → fill fields. Beans linkable to a hopper (machine grinder slot).
 *
 * Backed by WS handlers `melitta_barista/producers`, `.../beans`,
 * `.../beans/autofill` (all to be implemented).
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaBeans extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
    };
  }

  render() {
    return html`
      <section>
        <h2>Производители кофе и зёрна</h2>
        <p class="hint">
          CRUD производителей и сортов зёрен. Автозаполнение полей по запросу
          к LLM (HA conversation agent). Привязка зёрен к hopper-слотам машины.
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

customElements.define("melitta-beans", MelittaBeans);
