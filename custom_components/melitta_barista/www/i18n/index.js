/**
 * Panel i18n — resolver entry point.
 *
 * Each locale lives in its own ESM module under ./locales/ for diff-friendly
 * PR reviews and easy crowdsourced translations. English is the source of
 * truth; every other locale must carry the same key set (enforced by
 * tests/test_i18n_parity.py).
 *
 * Adding a new language:
 *   1. Drop ./locales/<HA-lang-code>.js exporting the same key set as en.js.
 *   2. Register it in the STRINGS dict below.
 *   3. The parity test will guard the key set.
 *
 * Untranslated keys silently fall back to English; entirely-unknown keys
 * are returned verbatim so a developer can spot them in the UI.
 */

import en from "./locales/en.js";
import ru from "./locales/ru.js";

const STRINGS = { en, ru };

/**
 * Resolve a translation key.
 *
 * @param {string} key       Dot-notated key like "status.firmware".
 * @param {string} [lang]    HA language code (e.g. "ru", "en"). Defaults to "en".
 * @param {Object} [params]  Optional substitution map; "{name}" tokens are
 *                           replaced with `params.name`.
 * @returns {string} The translated string, or the key itself if missing.
 */
export function t(key, lang = "en", params = null) {
  const dict = STRINGS[lang] || STRINGS.en;
  let value = dict[key];
  if (value === undefined) {
    value = STRINGS.en[key];
  }
  if (value === undefined) {
    return key;
  }
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      value = value.replaceAll(`{${k}}`, String(v));
    }
  }
  return value;
}

/** Convenience helper: returns a t() bound to a single language. */
export function makeT(lang) {
  return (key, params) => t(key, lang, params);
}

export const SUPPORTED_LANGUAGES = Object.keys(STRINGS);
