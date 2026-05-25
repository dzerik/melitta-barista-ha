/**
 * Shared CSS design tokens for the Melitta panel.
 *
 * Exposes `sharedStyles` — a Lit `css` template with :host-scoped CSS
 * custom properties that every panel component pulls in.
 *
 * Usage:
 *
 *   import { sharedStyles } from "../design-tokens.js";
 *   ...
 *   static get styles() { return [sharedStyles, css`...own styles...`]; }
 *
 * IMPORTANT: do NOT re-export sharedStyles via lit-base.js.
 *
 * lit-base.js is imported by every component as "./lit-base.js" WITHOUT
 * a cache-busting query parameter, so the browser ESM module-map plus
 * HA HTTP cache pin it indefinitely. Adding/changing exports there
 * yields "does not provide export named X" SyntaxErrors on user upgrade
 * (this was hit in 0.54.0 dev and is the reason this module exists).
 *
 * Tokens stick to HA theme vars where applicable (--primary-color,
 * --card-background-color, ...) and only add what HA does not provide
 * (spacing scale, radius, font-size scale, focus ring).
 */

import { css } from "./vendor/lit.js";

export const sharedStyles = css`
  :host {
    --mb-space-xs: 4px;
    --mb-space-sm: 8px;
    --mb-space-md: 12px;
    --mb-space-lg: 16px;
    --mb-space-xl: 24px;

    --mb-radius-sm: 4px;
    --mb-radius-md: 8px;

    --mb-focus-ring: 0 0 0 2px var(--primary-color);

    --mb-font-size-sm: 12px;
    --mb-font-size-md: 14px;
    --mb-font-size-lg: 16px;
  }
`;
