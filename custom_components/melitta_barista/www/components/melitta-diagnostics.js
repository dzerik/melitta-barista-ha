/**
 * Diagnostics tab.
 *
 * Surfaces what previously required ssh + grep on the HA host: ring-buffered
 * recent BLE errors and the last few notification frames, plus runtime
 * configuration (poll interval, timeouts, transport, last handshake time).
 *
 * Refreshes every 8 s while the tab is open. The "Clear log" button asks the
 * backend to drop the in-memory ring buffers — useful when reproducing an
 * issue and you want a clean baseline.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

const POLL_INTERVAL_MS = 8000;

class MelittaDiagnostics extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _data: { type: Object },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._data = null;
    this._error = "";
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
    if (this._timer) clearInterval(this._timer);
  }

  updated(changedProps) {
    if (changedProps.has("entryId") && this.entryId) this._load();
  }

  async _load() {
    if (!this.hass || !this.entryId) return;
    try {
      this._data = await this.hass.callWS({
        type: "melitta_barista/diagnostics",
        entry_id: this.entryId,
      });
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _clear() {
    try {
      await this.hass.callWS({
        type: "melitta_barista/diagnostics/clear",
        entry_id: this.entryId,
      });
      await this._load();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _formatTimestamp(epochSeconds) {
    if (!epochSeconds) return this._t("common.never");
    return new Date(epochSeconds * 1000).toLocaleString(this.lang || undefined);
  }

  _proxyLabel(kind) {
    if (kind === "remote") return this._t("diag.proxy_remote");
    if (kind === "local") return this._t("diag.proxy_local");
    return this._t("common.unknown");
  }

  _renderConfig() {
    const d = this._data;
    const rows = [
      [this._t("diag.address"), d.address],
      [this._t("diag.brand"), d.brand || this._t("common.unknown")],
      [this._t("diag.proxy"), this._proxyLabel(d.proxy)],
      [this._t("diag.poll_interval"),
        d.poll_interval ? `${d.poll_interval} s` : this._t("common.unknown")],
    ];
    return html`
      <div class="grid">
        ${rows.map(([k, v]) => html`
          <div class="row">
            <span class="label">${k}:</span>
            <span class="value">${v}</span>
          </div>
        `)}
      </div>
    `;
  }

  /**
   * Collapse adjacent rows whose dedup key matches into a single row showing
   * the latest timestamp and the repeat count. The input is reversed (newest
   * first) before grouping, so the rendered order remains "newest at top".
   */
  _collapseDuplicates(rows, keyFn) {
    const reversed = rows.slice().reverse();
    const out = [];
    for (const row of reversed) {
      const key = keyFn(row);
      const last = out.length ? out[out.length - 1] : null;
      if (last && last._key === key) {
        last._count += 1;
        continue;
      }
      out.push({ ...row, _key: key, _count: 1 });
    }
    return out;
  }

  _renderErrors() {
    const errors = this._data?.recent_errors || [];
    if (errors.length === 0) {
      return html`<div class="hint">${this._t("diag.no_errors")}</div>`;
    }
    const collapsed = this._collapseDuplicates(
      errors,
      (e) => `${e.source || ""}::${e.message || ""}`,
    );
    return html`
      <table class="log">
        <thead>
          <tr>
            <th>${this._t("status.last_update")}</th>
            <th>${this._t("diag.brand")}</th>
            <th>${this._t("recipes.name")}</th>
          </tr>
        </thead>
        <tbody>
          ${collapsed.map((err) => html`
            <tr>
              <td class="ts">
                ${this._formatTimestamp(err.ts)}
                ${err._count > 1 ? html`<span class="badge">×${err._count}</span>` : ""}
              </td>
              <td>${err.source || ""}</td>
              <td>${err.message || ""}</td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  _renderFrames() {
    const frames = this._data?.recent_frames || [];
    if (frames.length === 0) {
      return html`<div class="hint">${this._t("diag.no_frames")}</div>`;
    }
    const collapsed = this._collapseDuplicates(frames, (f) => f.hex);
    return html`
      <table class="log">
        <thead>
          <tr>
            <th>${this._t("status.last_update")}</th>
            <th class="num">${this._t("recipes.portion").replace(" (ml)", "")}</th>
            <th>HEX</th>
          </tr>
        </thead>
        <tbody>
          ${collapsed.map((f) => html`
            <tr>
              <td class="ts">
                ${this._formatTimestamp(f.ts)}
                ${f._count > 1 ? html`<span class="badge">×${f._count}</span>` : ""}
              </td>
              <td class="num">${f.len}</td>
              <td class="mono">${f.hex}</td>
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
    if (!this._data) {
      return html`<div class="hint">${this._t("common.loading")}</div>`;
    }
    if (!this._data.available) {
      return html`<div class="hint">${this._t("common.unknown")}</div>`;
    }

    return html`
      <section class="card">
        <header class="card-head">
          <h2>${this._t("diag.title")}</h2>
          <button class="action" @click=${() => this._clear()}>
            ${this._t("diag.clear")}
          </button>
        </header>
        ${this._renderConfig()}

        <h3>${this._t("diag.recent_errors")}</h3>
        ${this._renderErrors()}

        <h3>${this._t("diag.recent_frames")}</h3>
        ${this._renderFrames()}
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
      .label { color: var(--secondary-text-color); flex: 0 0 auto; white-space: nowrap; }
      .value { font-weight: 500; word-break: break-word; }
      .badge {
        display: inline-block;
        margin-left: 6px;
        padding: 1px 6px;
        background: var(--secondary-background-color);
        border-radius: 8px;
        font-size: 11px;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
      .error {
        margin: 12px 0;
        padding: 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
      }
      table.log {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
      }
      table.log th {
        text-align: left;
        padding: 6px 8px;
        color: var(--secondary-text-color);
        font-weight: 500;
        border-bottom: 1px solid var(--divider-color);
      }
      table.log td {
        padding: 4px 8px;
        border-bottom: 1px solid var(--divider-color);
        vertical-align: top;
      }
      table.log td.num { text-align: right; font-variant-numeric: tabular-nums; }
      table.log td.ts {
        white-space: nowrap;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      table.log td.mono {
        font-family: var(--code-font-family, monospace);
        font-size: 12px;
        word-break: break-all;
      }
      button.action {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.action:hover { opacity: 0.9; }
    `;
  }
}

customElements.define("melitta-diagnostics", MelittaDiagnostics);
