/**
 * Diagnostics / Logs tab — STUB.
 *
 * Will display: setup-phase timing, recent BLE errors (last N), last frames
 * buffer, ESPHome BLE proxy info. The goal is to surface what currently
 * requires `ssh + grep ha core logs`.
 *
 * Backed by WS handlers `melitta_barista/diagnostics`,
 * `melitta_barista/recent_errors`, `melitta_barista/recent_frames`
 * (all to be implemented).
 */

import { LitElement, html, css } from "../lit-base.js";

class MelittaDiagnostics extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
    };
  }

  render() {
    return html`
      <section>
        <h2>Диагностика</h2>
        <p class="hint">
          Здесь появятся: тайминги setup'а, последние BLE-ошибки, кадры с
          notify-канала, инфа про BLE proxy. Заглушка.
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

customElements.define("melitta-diagnostics", MelittaDiagnostics);
