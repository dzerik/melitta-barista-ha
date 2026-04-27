/**
 * Status / Health tab — STUB.
 *
 * Will display: BLE state (white/blue/red equivalent), last handshake time,
 * firmware revision, model name, capabilities (slots, family, supported features),
 * uptime, last status update timestamp.
 *
 * Backed by WS handler `melitta_barista/status` (to be implemented).
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaStatus extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
    };
  }

  render() {
    return html`
      <section>
        <h2>Состояние машины</h2>
        <p class="hint">
          Здесь появится BLE-состояние (white / blue / red), прошивка, модель,
          capabilities и время последнего handshake. Заглушка — наполняется в
          следующем коммите.
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

customElements.define("melitta-status", MelittaStatus);
