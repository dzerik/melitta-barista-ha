/**
 * <melitta-drink-icon> — inline-SVG renderer for UI Contract v1 IconSpec
 * (docs/UI_CONTRACT.md §3.6).
 *
 * Usage:
 *   <melitta-drink-icon .spec=${recipe.icon} size="28"></melitta-drink-icon>
 *
 * - .spec: IconSpec | null. `null`, structurally invalid input or an
 *   unknown `spec_version` render the neutral default cup (§5.3.2 —
 *   never throw, never blank).
 * - size: rendered width/height in px (square), default 28.
 *
 * Client rendering contract implemented here:
 * - liquid drawn up to `fill_level` of the glass interior; layers (and
 *   foam, topmost) stacked bottom→up by `fraction`, normalized so the
 *   ±0.02 server rounding remainder folds into the last entry;
 * - unknown `glass` → `cup` geometry; unknown layer `role` → neutral
 *   grey at the layer's `intensity`;
 * - `intensity` darkens a role's base color via a black overlay;
 * - `color_hint` (additive layers) is escaped color DATA: strict
 *   `#RRGGBB` or it is ignored;
 * - colors are CSS custom properties (--mb-icon-*) with literal
 *   fallbacks so both HA themes work; the glass outline and steam use
 *   `currentColor` and inherit the surrounding text color.
 *
 * The element is decorative (`aria-hidden`): the recipe name it sits
 * next to carries the accessible label. No i18n strings.
 */

import { LitElement, html, svg, css } from "../../lit-base.js";

/** Strict #RRGGBB check — color_hint is data, never markup (§3.6). */
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** Nominal volumes (ml), normative for spec_version 1 (§3.6). */
const NOMINAL_ML = { espresso_cup: 60, cup: 220, tall_glass: 320 };

/**
 * Glass geometries in a 24×24 viewBox. `clip` is the interior polygon
 * liquid rects are clipped to; `top`/`bottom` bound the fillable height.
 * Unknown glass tokens fall back to "cup".
 */
const GLASSES = {
  espresso_cup: {
    outline: "M6.5 11.5 L7.8 20.5 L16.2 20.5 L17.5 11.5 Z",
    extra: "M5 22.5 L19 22.5",
    clip: "7.1,12.1 8.3,19.9 15.7,19.9 16.9,12.1",
    top: 12.1, bottom: 19.9, steamY: 10,
  },
  cup: {
    outline: "M4.5 8.5 L6 20.5 L16 20.5 L17.5 8.5 Z",
    extra: "M17.3 11 C 20.5 11, 20.5 16, 16.8 16",
    clip: "5.1,9.1 6.5,19.9 15.5,19.9 16.9,9.1",
    top: 9.1, bottom: 19.9, steamY: 7,
  },
  tall_glass: {
    outline: "M8 3.5 L9 21.5 L15 21.5 L16 3.5 Z",
    extra: "",
    clip: "8.6,4.1 9.55,20.9 14.45,20.9 15.4,4.1",
    top: 4.1, bottom: 20.9, steamY: 2.5,
  },
};

/** Role → base fill; themeable via CSS vars with literal fallbacks. */
const ROLE_FILLS = {
  coffee: "var(--mb-icon-coffee, #6f4e37)",
  milk: "var(--mb-icon-milk, #f3e9dc)",
  water: "var(--mb-icon-water, #9ecbe8)",
  additive: "var(--mb-icon-additive, #c9924b)",
  milk_foam: "var(--mb-icon-foam, #f8f0e0)",
};
const NEUTRAL_FILL = "var(--mb-icon-neutral, #9e9e9e)";
const CREMA_FILL = "var(--mb-icon-crema, #d9a05b)";

/** Neutral default drink for missing/invalid specs (§5.3.2). */
const DEFAULT_SPEC = {
  spec_version: 1,
  glass: "cup",
  fill_level: 0.6,
  layers: [{ role: "coffee", fraction: 1.0, intensity: 0.5, crema: true }],
  foam: null,
  steam: true,
};

/** True when `spec` is a renderable spec_version-1 IconSpec. */
export function isRenderableSpec(spec) {
  return Boolean(
    spec
    && typeof spec === "object"
    && spec.spec_version === 1
    && Array.isArray(spec.layers)
    && spec.layers.length > 0,
  );
}

/** Clamp helper for fractions/levels. */
function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

/**
 * Normalize a spec into a draw plan: fill level + bottom→top stack of
 * {role, fraction, intensity, colorHint, crema} entries whose fractions
 * sum to exactly 1 (server remainder folded into the last entry).
 */
export function computeStack(spec) {
  const entries = [];
  for (const layer of spec.layers) {
    if (!layer || typeof layer !== "object") continue;
    const hint = typeof layer.color_hint === "string"
      && HEX_COLOR.test(layer.color_hint) ? layer.color_hint : null;
    entries.push({
      role: typeof layer.role === "string" ? layer.role : "unknown",
      fraction: Number(layer.fraction) > 0 ? Number(layer.fraction) : 0,
      intensity: clamp(Number(layer.intensity) || 0, 0, 1),
      colorHint: hint,
      crema: layer.crema === true,
    });
  }
  if (spec.foam && typeof spec.foam === "object") {
    entries.push({
      role: "milk_foam",
      fraction: Number(spec.foam.fraction) > 0 ? Number(spec.foam.fraction) : 0,
      intensity: 0,
      colorHint: null,
      crema: false,
    });
  }
  if (entries.length === 0) return null;
  let sum = entries.reduce((acc, e) => acc + e.fraction, 0);
  if (sum <= 0) {
    for (const e of entries) e.fraction = 1 / entries.length;
    sum = 1;
  }
  for (const e of entries) e.fraction /= sum;

  let fillLevel = Number(spec.fill_level);
  if (!(fillLevel > 0)) {
    const nominal = Number(NOMINAL_ML[spec.glass]) || NOMINAL_ML.cup;
    const total = Number(spec.total_ml) > 0 ? Number(spec.total_ml) : nominal;
    fillLevel = total / nominal;
  }
  return { fillLevel: clamp(fillLevel, 0.01, 1.0), entries };
}

class MelittaDrinkIcon extends LitElement {
  static get properties() {
    return {
      spec: { attribute: false },
      size: { type: Number },
    };
  }

  constructor() {
    super();
    this.spec = null;
    this.size = 28;
  }

  _renderLiquid(glass, plan) {
    const interiorH = glass.bottom - glass.top;
    const filledH = interiorH * plan.fillLevel;
    const rects = [];
    let y = glass.bottom;
    plan.entries.forEach((entry, i) => {
      const h = filledH * entry.fraction;
      if (h <= 0) return;
      y -= h;
      const rectY = i === plan.entries.length - 1 ? y : y - 0.15; // seam overlap
      const fill = entry.colorHint
        || (Object.hasOwn(ROLE_FILLS, entry.role) ? ROLE_FILLS[entry.role] : null)
        || NEUTRAL_FILL; // unknown role → neutral grey layer
      rects.push(svg`<rect x="0" y=${rectY.toFixed(2)} width="24"
        height=${(glass.bottom - rectY).toFixed(2)} fill=${fill}
        fill-opacity=${entry.role === "water" ? 0.55 : 1}></rect>`);
      // intensity as darkness: black overlay scaled by the hint value.
      if (entry.intensity > 0 && entry.role !== "milk_foam") {
        rects.push(svg`<rect x="0" y=${rectY.toFixed(2)} width="24"
          height=${(glass.bottom - rectY).toFixed(2)} fill="#000000"
          fill-opacity=${(0.45 * entry.intensity).toFixed(3)}></rect>`);
      }
      // crema: thin band on top of the flagged coffee layer.
      if (entry.crema) {
        const bandH = Math.max(0.7, h * 0.14);
        rects.push(svg`<rect x="0" y=${y.toFixed(2)} width="24"
          height=${bandH.toFixed(2)} fill=${CREMA_FILL}></rect>`);
      }
    });
    return rects;
  }

  _renderSteam(glass) {
    const y = glass.steamY;
    const wisp = (x) => svg`<path fill="none" stroke="currentColor"
      stroke-width="1" stroke-linecap="round" opacity="0.45"
      d="M ${x} ${y} c 1 -1.2, -1 -2.4, 0 -3.6"></path>`;
    return [wisp(10.2), wisp(13.8)];
  }

  render() {
    const spec = isRenderableSpec(this.spec) ? this.spec : DEFAULT_SPEC;
    // Unknown glass → cup geometry and nominal volume (§3.6);
    // Object.hasOwn guards against inherited-key lookups ("constructor").
    const glassKey = Object.hasOwn(GLASSES, spec.glass) ? spec.glass : "cup";
    const glass = GLASSES[glassKey] || GLASSES["cup"];
    const plan = computeStack(spec) || computeStack(DEFAULT_SPEC);
    const size = Number(this.size) > 0 ? Number(this.size) : 28;
    return html`<svg viewBox="0 0 24 24" width=${size} height=${size}
        role="img" aria-hidden="true">
      <clipPath id="mb-liquid-clip"><polygon points=${glass.clip}></polygon></clipPath>
      <g clip-path="url(#mb-liquid-clip)">${this._renderLiquid(glass, plan)}</g>
      <path d=${glass.outline} fill="none" stroke="currentColor"
        stroke-width="1.3" stroke-linejoin="round" opacity="0.8"></path>
      ${glass.extra ? svg`<path d=${glass.extra} fill="none"
        stroke="currentColor" stroke-width="1.3" stroke-linecap="round"
        opacity="0.8"></path>` : ""}
      ${spec.steam === true ? this._renderSteam(glass) : ""}
    </svg>`;
  }

  static get styles() {
    return css`
      :host {
        display: inline-flex;
        flex: none;
        line-height: 0;
        color: var(--secondary-text-color, currentColor);
      }
    `;
  }
}

if (!customElements.get("melitta-drink-icon")) {
  customElements.define("melitta-drink-icon", MelittaDrinkIcon);
}
