/**
 * Settings tab.
 *
 * - LLM model picker: dropdown of HA conversation agents; the selected
 *   agent_id is persisted via `melitta_barista/sommelier/settings/set`
 *   (key=`llm_agent_id`) and reused by both Sommelier generation and Bean
 *   autofill. Choosing "default" stores an empty string, which makes the
 *   backend fall back to HA's default agent.
 *
 * - Prompt templates editor: every LLM-bound request type has a default
 *   prompt bundled in the integration; users can override it. Saving an
 *   empty / identical template falls back to the default. The list of slots
 *   comes from `melitta_barista/prompts/list` so this UI auto-grows when new
 *   slots are introduced.
 */

import { LitElement, html, css } from "../lit-base.js";
import { t } from "../i18n.js";

class MelittaSettings extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      lang: { type: String },
      _agents: { type: Array },
      _selectedAgent: { type: String },
      _prompts: { type: Array },
      _drafts: { type: Object },
      _info: { type: String },
      _error: { type: String },
    };
  }

  constructor() {
    super();
    this._agents = [];
    this._selectedAgent = "";
    this._prompts = [];
    this._drafts = {};
    this._info = "";
    this._error = "";
  }

  _t(key, params) {
    return t(key, this.lang || "en", params);
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadAll();
  }

  async _loadAll() {
    try {
      const [a, s, p] = await Promise.all([
        this.hass.callWS({ type: "melitta_barista/llm/agents" }),
        this.hass.callWS({ type: "melitta_barista/sommelier/settings/get" }),
        this.hass.callWS({ type: "melitta_barista/prompts/list" }),
      ]);
      this._agents = a.agents || [];
      this._selectedAgent = (s.settings || {}).llm_agent_id || "";
      this._prompts = p.prompts || [];
      const drafts = {};
      for (const item of this._prompts) drafts[item.slot] = item.template;
      this._drafts = drafts;
      this._error = "";
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _saveAgent(agentId) {
    this._selectedAgent = agentId;
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/sommelier/settings/set",
        key: "llm_agent_id",
        value: agentId,
      });
      this._info = this._t("settings.saved");
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _savePrompt(slot) {
    const template = this._drafts[slot] ?? "";
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/prompts/save",
        slot, template,
      });
      this._info = this._t("settings.saved");
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _resetPrompt(slot) {
    this._info = "";
    try {
      await this.hass.callWS({
        type: "melitta_barista/prompts/reset",
        slot,
      });
      this._info = this._t("settings.reset_done");
      await this._loadAll();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  _onPromptInput(slot, value) {
    this._drafts = { ...this._drafts, [slot]: value };
  }

  render() {
    return html`
      <section class="card">
        <h2>${this._t("settings.title")}</h2>
        ${this._error ? html`<div class="error">${this._error}</div>` : ""}
        ${this._info ? html`<div class="info">${this._info}</div>` : ""}

        <h3>${this._t("settings.llm_agent")}</h3>
        <p class="help">${this._t("settings.llm_help")}</p>
        <select class="agent"
          .value=${this._selectedAgent}
          @change=${(e) => this._saveAgent(e.target.value)}>
          <option value="" ?selected=${!this._selectedAgent}>— HA default —</option>
          ${this._agents.map((a) => html`
            <option value=${a.id} ?selected=${a.id === this._selectedAgent}>
              ${a.name || a.id}
            </option>
          `)}
        </select>

        <h3>${this._t("settings.prompts")}</h3>

        <details class="help" open>
          <summary>${this._t("settings.help_title")}</summary>
          <div class="help-body">
            <p><strong>${this._t("settings.help_syntax")}.</strong>
              ${this._t("settings.help_syntax_text")}</p>
            <p>${this._t("settings.help_schema")}</p>
            <p>${this._t("settings.help_smartchain")}</p>
          </div>
        </details>

        ${this._prompts.length === 0
          ? html`<div class="hint">${this._t("common.empty")}</div>`
          : this._prompts.map((p) => html`
            <details class="prompt" ?open=${!p.is_default}>
              <summary>
                <code>${p.slot}</code>
                ${p.is_default ? html`<span class="badge">${this._t("settings.prompt_default")}</span>` : ""}
              </summary>

              <div class="placeholders">
                <span class="ph-label">${this._t("settings.help_placeholders")}:</span>
                ${(p.placeholders || []).length === 0
                  ? html`<span class="ph-empty">${this._t("settings.help_no_placeholders")}</span>`
                  : (p.placeholders || []).map((ph) => html`
                    <code class="ph">{${ph.name}}</code>
                    <span class="ph-desc">— ${ph.desc}</span>
                  `)}
              </div>

              <textarea
                rows="10"
                .value=${this._drafts[p.slot] ?? ""}
                @input=${(e) => this._onPromptInput(p.slot, e.target.value)}
              ></textarea>
              <div class="form-actions">
                <button class="ghost" @click=${() => this._resetPrompt(p.slot)}>
                  ${this._t("settings.prompt_reset")}
                </button>
                <button class="primary" @click=${() => this._savePrompt(p.slot)}>
                  ${this._t("settings.prompt_save")}
                </button>
              </div>
              ${p.schema ? html`
                <details class="schema">
                  <summary>JSON Schema (auto-appended, read-only)</summary>
                  <pre>${JSON.stringify(p.schema, null, 2)}</pre>
                </details>
              ` : ""}
            </details>
          `)}
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
      h2 { margin: 0 0 12px; font-size: 18px; }
      h3 {
        margin: 24px 0 4px;
        font-size: 14px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .help {
        margin: 0 0 8px;
        color: var(--secondary-text-color);
        font-size: 13px;
      }
      select.agent {
        width: 100%;
        max-width: 480px;
        padding: 8px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-size: 14px;
      }
      details.prompt {
        margin-top: 8px;
        background: var(--secondary-background-color);
        border-radius: 4px;
        padding: 8px 12px;
      }
      details.prompt summary {
        cursor: pointer;
        font-size: 13px;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      details.prompt summary code {
        font-family: var(--code-font-family, monospace);
      }
      .badge {
        font-size: 11px;
        padding: 1px 6px;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border-radius: 8px;
      }
      details.help {
        margin: 8px 0 16px;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 13px;
      }
      details.help summary { cursor: pointer; font-weight: 500; }
      details.help .help-body { padding-top: 6px; }
      details.help p { margin: 6px 0; line-height: 1.45; }
      details.help code, details.help strong { font-weight: 500; }

      .placeholders {
        font-size: 12px;
        color: var(--secondary-text-color);
        background: var(--primary-background-color);
        border-radius: 4px;
        padding: 6px 10px;
        margin: 8px 0;
        display: flex;
        flex-wrap: wrap;
        gap: 4px 6px;
        align-items: baseline;
      }
      .ph-label {
        font-weight: 500;
        margin-right: 4px;
      }
      .ph-empty { font-style: italic; }
      .ph {
        background: var(--secondary-background-color);
        padding: 1px 6px;
        border-radius: 3px;
        font-family: var(--code-font-family, monospace);
        color: var(--primary-text-color);
      }
      .ph-desc { margin-right: 6px; }

      details.schema {
        margin-top: 8px;
        background: var(--primary-background-color);
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 11px;
      }
      details.schema summary {
        cursor: pointer;
        color: var(--secondary-text-color);
        font-size: 11px;
      }
      details.schema pre {
        margin: 6px 0 0;
        white-space: pre-wrap;
        word-break: break-word;
        font-family: var(--code-font-family, monospace);
        color: var(--secondary-text-color);
      }
      details.prompt textarea {
        width: 100%;
        margin-top: 8px;
        padding: 8px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--code-font-family, monospace);
        font-size: 12px;
        line-height: 1.4;
        resize: vertical;
      }
      .form-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 8px;
      }
      button.primary {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      button.ghost {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
      }
      .info {
        margin: 8px 0;
        padding: 8px 12px;
        background: var(--info-color, #2196f3);
        color: var(--text-primary-color);
        border-radius: 4px;
        font-size: 13px;
      }
      .error {
        margin: 8px 0;
        padding: 8px 12px;
        background: var(--error-color);
        color: var(--text-primary-color);
        border-radius: 4px;
        font-size: 13px;
      }
      .hint { color: var(--secondary-text-color); padding: 8px 0; }
    `;
  }
}

customElements.define("melitta-settings", MelittaSettings);
