/**
 * Server-served machine-domain display strings — pure synchronous registry.
 *
 * Half (a) of the UI Contract §6.3.5.6 split: this module holds the merged
 * string map returned by the `melitta_barista/i18n/get` WebSocket command
 * and exposes synchronous lookups implementing the normative per-key
 * preference order (server string → client bundle string → humanized raw
 * token). It has zero hass coupling — the fetch/cache half lives in the
 * panel shell (melitta-panel.js), which feeds this registry via
 * setServerStrings() once per locale.
 *
 * Keys are flat, dot-joined and byte-equal to the contract tokens they
 * describe (§6.3.1): `status.*` embeds UPPER_SNAKE, `values.*` /
 * `recipes.*` / `actions.*` embed lower_snake. Never case-fold keys.
 */

let _strings = null;

/**
 * Install the merged server string map (or clear it with null).
 * Non-object values are treated as "no server strings".
 */
export function setServerStrings(map) {
  _strings =
    map && typeof map === "object" && !Array.isArray(map) ? map : null;
}

/** Server string for a flat dot-joined key, or null when absent. */
export function serverString(key) {
  const value = _strings ? _strings[key] : undefined;
  return typeof value === "string" ? value : null;
}

/** Forget all server strings (locale switch, tests). */
export function resetServerStrings() {
  _strings = null;
}

/**
 * Last-resort token humanizer (§5.3.2): underscores become spaces, the
 * text is lower-cased and the first letter capitalized. Works for both
 * UPPER_SNAKE status tokens and lower_snake value tokens.
 */
export function humanizeToken(token) {
  const text = String(token ?? "").replace(/_/g, " ").toLowerCase().trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/**
 * Resolve a display label with the §6.3.5 preference order:
 * server string → client bundle string → humanized raw token.
 *
 * Pass null (or an empty string) as bundleValue when the panel bundle has
 * no entry for the token so the chain falls through to humanization.
 */
export function labelFor(serverKey, bundleValue, token) {
  const server = serverString(serverKey);
  if (server !== null) return server;
  if (typeof bundleValue === "string" && bundleValue) return bundleValue;
  return humanizeToken(token);
}

/**
 * Family-scoped value label (§6.3.5.7 shape): server key
 * `values.<family>.<token>` → provided bundle string → humanized token.
 * Family scoping matters because bare tokens collide across families
 * ("none", "standard").
 */
export function displayNameFor(family, token, bundleValue = null) {
  return labelFor(`values.${family}.${token}`, bundleValue, token);
}
