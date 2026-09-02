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
import bg from "./locales/bg.js";
import bs from "./locales/bs.js";
import cs from "./locales/cs.js";
import da from "./locales/da.js";
import de from "./locales/de.js";
import el from "./locales/el.js";
import es from "./locales/es.js";
import et from "./locales/et.js";
import fi from "./locales/fi.js";
import fr from "./locales/fr.js";
import hr from "./locales/hr.js";
import hu from "./locales/hu.js";
import it from "./locales/it.js";
import lt from "./locales/lt.js";
import lv from "./locales/lv.js";
import mk from "./locales/mk.js";
import nb from "./locales/nb.js";
import nl from "./locales/nl.js";
import pl from "./locales/pl.js";
import pt from "./locales/pt.js";
import ro from "./locales/ro.js";
import sk from "./locales/sk.js";
import sl from "./locales/sl.js";
import sr from "./locales/sr.js";
import sv from "./locales/sv.js";
import tr from "./locales/tr.js";
import uk from "./locales/uk.js";

const STRINGS = { en, ru, bg, bs, cs, da, de, el, es, et, fi, fr, hr, hu, it, lt, lv, mk, nb, nl, pl, pt, ro, sk, sl, sr, sv, tr, uk };

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
