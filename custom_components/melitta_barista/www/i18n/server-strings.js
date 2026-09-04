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
 *
 * Since 0.94 the served set also covers the machine-domain families of
 * §6.3.7 — `wizard.*` brew-guide vocabulary, `status.*.<TOKEN>.description`
 * state descriptions, `sommelier.error.<code>` hints and the
 * `sommelier.<milk|syrup|topping|liqueur|note>.<token>` suggestion labels.
 * The panel bundles under www/i18n/locales keep every one of those keys as
 * the tier-2 fallback for pre-0.94 servers and transient i18n failures.
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

/**
 * Substitute `{name}` placeholders into a server-served template.
 *
 * Server strings carry the same placeholder names, count and
 * substitution semantics as their client-bundle counterparts (§6.3.7),
 * but they never pass through the bundle resolver `t()`, so the server
 * tier needs its own substitution pass. Missing params are left as
 * literal `{name}` spans rather than blanked, so a wiring mistake is
 * visible instead of silent.
 */
export function formatString(template, params) {
  let value = template == null ? "" : String(template);
  if (!params) return value;
  for (const [key, replacement] of Object.entries(params)) {
    value = value.replaceAll(`{${key}}`, String(replacement));
  }
  return value;
}

/**
 * Label for a value of a free-form suggestion field: server string
 * `sommelier.<family>.<value>` → optional client bundle string → the
 * user's own text VERBATIM.
 *
 * The five suggestion families served since 0.94 (`milk`, `syrup`,
 * `topping`, `liqueur`, `note`, §6.3.7) label WELL-KNOWN tokens only.
 * Those fields stay free text by design (§9.2.4): a value the user typed
 * has no key and MUST render exactly as typed — never humanized, never
 * coerced to a token, never dropped. That is why this helper falls back
 * to the raw value instead of going through humanizeToken().
 */
export function freeFormLabel(family, value, bundleValue = null) {
  const raw = value == null ? "" : String(value);
  if (!raw) return "";
  const server = serverString(`sommelier.${family}.${raw}`);
  if (server !== null) return server;
  if (typeof bundleValue === "string" && bundleValue) return bundleValue;
  return raw;
}
