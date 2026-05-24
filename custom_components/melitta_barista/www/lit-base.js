/**
 * Self-contained lit 3.x re-export + shared design tokens.
 *
 * Components import { LitElement, html, css, sharedStyles } from "./lit-base.js"
 * and prepend `sharedStyles` to their static styles array, e.g.:
 *
 *   static get styles() { return [sharedStyles, css`...`]; }
 *
 * History: panels used to grab Lit via prototype-chain of ha-panel-lovelace,
 * which broke on fresh HA installs (issue #32). The vendored bundle in
 * vendor/lit.js works uniformly.
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

export { designTokens as sharedStyles } from "./design-tokens.js";
