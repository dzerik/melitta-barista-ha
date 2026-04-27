/**
 * Sommelier tab — STUB.
 *
 * Will provide a chat-style UI for generating recipes via the LLM agent
 * (existing `sommelier_api.py::ws_generate`), then a "Brew this" button that
 * converts the structured recipe into an HE freestyle payload and triggers
 * `client.brew_freestyle()`. The `structured recipe → HE payload` mapping is
 * the last missing end-to-end hop tracked in 0.49.3 README.
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaSommelier extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
    };
  }

  render() {
    return html`
      <section>
        <h2>AI Сомелье</h2>
        <p class="hint">
          Чат-style UI для запроса рецепта у LLM с учётом текущих зёрен,
          добавок и предпочтений. После генерации — кнопка «Сварить»
          (structured recipe → HE-payload → brew_freestyle).
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

customElements.define("melitta-sommelier", MelittaSommelier);
