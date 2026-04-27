/**
 * Status / Health tab.
 *
 * Renders an at-a-glance card of the machine: BLE connection (with the
 * Melitta-style white/blue/red analogue), current process / manipulation
 * prompt, firmware, model, family, capabilities, total cup counter and
 * per-recipe counters, plus the time of the last successful HU handshake.
 *
 * Polls `melitta_barista/status` every 5 seconds so brews show up live.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const POLL_INTERVAL_MS = 5000;

class MelittaStatus extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _data: { type: Object },
      _error: { type: String },
      _loading: { type: Boolean },
    };
  }

  constructor() {
    super();
    this._data = null;
    this._error = "";
    this._loading = false;
    this._timer = null;
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._timer = setInterval(() => this._load(), POLL_INTERVAL_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  updated(changedProps) {
    if (changedProps.has("entryId") && this.entryId) {
      this._load();
    }
  }

  async _load() {
    if (!this.hass || !this.entryId) return;
    this._loading = true;
    try {
      this._data = await this.hass.callWS({
        type: "melitta_barista/status",
        entry_id: this.entryId,
      });
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    } finally {
      this._loading = false;
    }
  }

  /**
   * Map the live state to a coloured pill that matches the BLE icon on the
   * machine (white = idle, blue = connected & responding, red = handshake
   * stalled). The names map to the `--status-{kind}-bg` CSS variables below.
   */
  _connectionColor() {
    if (!this._data || !this._data.available) return "white";
    if (!this._data.connected) return "white";
    if (!this._data.status || !this._data.status.process) return "red";
    return "blue";
  }

  _formatTimestamp(epochSeconds) {
    if (!epochSeconds) return this._t("common.never");
    const date = new Date(epochSeconds * 1000);
    return date.toLocaleString(this.lang || undefined);
  }

  _renderRow(label, value) {
    return html`
      <div class="row">
        <span class="label">${label}:</span>
        <span class="value">${value ?? this._t("common.unknown")}</span>
      </div>
    `;
  }

  _renderStatusBlock() {
    const status = this._data?.status;
    if (!status) {
      return html`<div class="hint">${this._t("status.no_status")}</div>`;
    }
    return html`
      ${this._renderRow(this._t("status.process"), status.process)}
      ${status.manipulation && status.manipulation !== "NONE"
        ? this._renderRow(this._t("status.manipulation"), status.manipulation)
        : ""}
    `;
  }

  _renderCupCounters() {
    const counters = this._data?.cup_counters || {};
    const entries = Object.entries(counters).filter(([, v]) => v > 0);
    if (entries.length === 0) {
      return html`<div class="hint">${this._t("common.empty")}</div>`;
    }
    entries.sort((a, b) => b[1] - a[1]);
    return html`
      <table class="counters">
        <tbody>
          ${entries.map(([recipe, count]) => html`
            <tr>
              <td>${recipe}</td>
              <td class="num">${count}</td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  render() {
    if (this._error) {
      return html`<div class="error">${this._t("common.error")}: ${this._error}</div>`;
    }
    if (!this._data && this._loading) {
      return html`<div class="hint">${this._t("common.loading")}</div>`;
    }
    if (!this._data || !this._data.available) {
      return html`<div class="hint">${this._t("common.unknown")}</div>`;
    }

    const data = this._data;
    const colorKind = this._connectionColor();

    return html`
      <section class="card">
        <header class="card-head">
          <h2>${this._t("status.title")}</h2>
          <span class="pill ${colorKind}">
            ${data.connected ? this._t("status.connected") : this._t("status.disconnected")}
          </span>
        </header>

        <div class="grid">
          ${this._renderRow(this._t("status.ble_state"), data.address)}
          ${this._renderRow(this._t("status.firmware"), data.firmware)}
          ${this._renderRow(this._t("status.model"),
            data.model || data.capabilities?.model_name)}
          ${this._renderRow(this._t("status.family"), data.capabilities?.family_key)}
          ${this._renderRow(this._t("status.slots"), data.capabilities?.my_coffee_slots)}
          ${this._renderRow(this._t("status.machine_type"), data.machine_type)}
          ${this._renderRow(this._t("status.profile_active"), data.active_profile)}
          ${this._renderRow(this._t("status.selected_recipe"),
            data.selected_recipe ?? this._t("common.unknown"))}
          ${this._renderRow(this._t("diag.handshake"),
            this._formatTimestamp(data.last_handshake_at))}
          ${this._renderRow(this._t("status.cup_total"), data.total_cups)}
        </div>

        <h3>${this._t("status.machine_state")}</h3>
        <div class="grid">${this._renderStatusBlock()}</div>

        ${data.dis ? html`
          <h3>${this._t("status.dis")}</h3>
          <div class="grid">
            ${Object.entries(data.dis).map(([k, v]) => this._renderRow(k, v))}
          </div>
        ` : ""}

        <h3>${this._t("status.cup_by_recipe")}</h3>
        ${this._renderCupCounters()}
      </section>
    `;
  }

  static get styles() {
    return css`
      .card {
        background: var(--card-background-color);
        border-radius: 8px;
        padding: 16px 20px;
        box-shadow: var(--ha-card-box-shadow);
      }
      .card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      }
      h2 { margin: 0; font-size: 18px; }
      h3 {
        margin: 20px 0 8px;
        font-size: 14px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      /* Compact key-value list: label and value sit on the same line, side
         by side, with a small gap. Pages stack vertically — no two-column
         layout that pushes value to the right edge of the screen. */
      .grid {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        padding: 4px 0;
        border-bottom: 1px solid var(--divider-color);
        font-size: 13px;
        line-height: 1.35;
      }
      .row:last-child { border-bottom: none; }
      .label {
        color: var(--secondary-text-color);
        flex: 0 0 auto;
        white-space: nowrap;
      }
      .value {
        font-weight: 500;
        word-break: break-word;
      }
      .pill {
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 13px;
        font-weight: 500;
        color: white;
      }
      .pill.white  { background: #888; }
      .pill.blue   { background: var(--info-color, #2196f3); }
      .pill.red    { background: var(--error-color, #f44336); }
      .hint { color: var(--secondary-text-color); font-size: 14px; padding: 8px 0; }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }
      table.counters {
        width: 100%;
        border-collapse: collapse;
      }
      table.counters td {
        padding: 6px 0;
        border-bottom: 1px solid var(--divider-color);
      }
      table.counters td.num {
        text-align: right;
        font-weight: 500;
      }
    `;
  }
}

customElements.define("melitta-status", MelittaStatus);
