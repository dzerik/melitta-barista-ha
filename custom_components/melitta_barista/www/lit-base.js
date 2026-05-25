/**
 * Self-contained lit 3.x re-export.
 *
 * Раньше компоненты панели получали `LitElement`, `html` и `css` через
 * trick `Object.getPrototypeOf(customElements.get("ha-panel-lovelace"))`.
 * Этот подход зависит от того, был ли к моменту загрузки панели
 * зарегистрирован `ha-panel-lovelace` с активным LitElement-prototype.
 *
 * На современном HA frontend эти символы уже не проксируются через
 * prototype — hack рассыпается в "чистых" установках без дополнительных
 * HACS-карт, которые побочно их гидратируют (issue #32).
 *
 * Теперь: статический vendored bundle `vendor/lit.js` (~16 КБ,
 * self-contained) — работает одинаково у любого пользователя, без
 * зависимости от окружения.
 *
 * INVARIANT: contents of this file MUST NOT change between releases.
 *
 * Why: this file is imported by every component via the bare path
 * "../lit-base.js", which means it carries NO cache-busting query
 * parameter. The browser ESM module-map and HA HTTP cache pin it
 * aggressively. If you change exports here, existing users hit
 * "does not provide export named X" SyntaxErrors after upgrade.
 *
 * If you need to add a new shared utility/style:
 *   - Put it in its OWN module (e.g. design-tokens.js, mb-mixins.js).
 *   - Components import it directly from that module.
 *   - The new module's URL is fresh => cache works correctly.
 * See design-tokens.js for the canonical example.
 */
export {
  LitElement,
  html,
  css,
  ReactiveElement,
  CSSResult,
  unsafeCSS,
  nothing,
  noChange,
  render,
  svg,
  mathml,
} from "./vendor/lit.js";
