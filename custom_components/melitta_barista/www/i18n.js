/**
 * Panel i18n — backwards-compatible re-export.
 *
 * The actual resolver and per-locale dictionaries moved to ./i18n/ in
 * 0.74.0 so each language lives in its own diff-friendly file. This
 * module re-exports the public API verbatim so existing consumers
 * (`import { t } from "./i18n.js"` across panel components) keep
 * working without changes.
 *
 * New code is welcome to import from `./i18n/index.js` directly.
 */

export { t, makeT, SUPPORTED_LANGUAGES } from "./i18n/index.js";
