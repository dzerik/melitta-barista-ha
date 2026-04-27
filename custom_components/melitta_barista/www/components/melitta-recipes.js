/**
 * Recipes / DirectKey editor tab — STUB.
 *
 * Will display: 24 base recipes (HR/HS) and DirectKey recipes (9 profiles ×
 * 7 categories), each editable. Save uses existing services
 * `nivona_write_recipe_param`, `save_directkey`.
 *
 * Backed by WS handlers `melitta_barista/recipes`,
 * `melitta_barista/directkey` (to be implemented).
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaRecipes extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
    };
  }

  render() {
    return html`
      <section>
        <h2>Рецепты и DirectKey</h2>
        <p class="hint">
          Визуальный редактор 24 базовых рецептов и DirectKey-профилей.
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

customElements.define("melitta-recipes", MelittaRecipes);
