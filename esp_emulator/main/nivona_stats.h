// Per-family stat-ID tables — authoritative.
//
// Ported line-by-line from `StatisticsFactory.GetAvailableStatisticsFor*`
// in EugsterMobileApp.decompiled.cs:9146-9306 (audit V2 Focus 5/12).
// These are the exact HR IDs the Nivona Android app reads for
// per-recipe counters, maintenance counters, and maintenance-gauge
// "dependent setting" references. No two families agree fully — 600
// and 700/79X have NO cumulative counters at all; 1000-family uses
// 216=clean_coffee where 8000 and 900 use 214; 1000-family uses
// 222=descale where 8000/900 use 220.
//
// Writing a counter ID that doesn't exist for the current family is
// harmless (emulator just caches an unused HR entry), but reading
// it from the app would surface a stat sensor that the real machine
// never updates. The emulator uses these tables to:
//
//   1. Gate cup-counter bumps in nivona_brew.c:
//      if (stats_has_recipe_counter(fam, selector)) bump(200+selector).
//   2. Resolve family-specific cycle counters in nivona_maint_cycle.c:
//      plan->stat_counter = stats_descale_id(fam) for a descale run.

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#include "nivona_families.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *family_key;      // matches nivona_family_t.key

    // Recipe counter bitmap. Bit N (0..24) set means HR id (200+N)
    // is a valid per-recipe counter for this family. Use
    // `nivona_stats_has_recipe_counter` to test.
    uint32_t    recipe_id_mask;

    // Cumulative / maintenance counters. 0 means "not present".
    int16_t     total_id;            // 213 for 8000/900, 215 for 1000,
                                     // 0 for 700/79X/600 (no cumulative)
    int16_t     clean_coffee_id;     // 214 (8000/900), 216 (1000), 0 else
    int16_t     clean_frother_id;    // 215 (8000/900), 217 (1000), 0 else
    int16_t     rinse_id;            // 216 (8000/900), 218 (1000), 0 else
    int16_t     rinse_frother_id;    // 217 (900), 219 (1000), 0 else
    int16_t     rinse_filter_id;     // 218 (900), 220 (1000), 0 else
    int16_t     filter_change_id;    // 219 (8000/900), 221 (1000), 0 else
    int16_t     descale_id;          // 220 (8000/900), 222 (1000), 0 else
    int16_t     via_app_id;          // 221 (8000/900), 223 (1000), 0 else
    int16_t     pot_id;              // 224 (1000 only), 0 else

    // Maintenance gauges are universal: 600/601 (descale), 610/611
    // (BU clean), 620/621 (frother clean), 640/641 (filter). Only the
    // "dependent setting" ID behind the Filterwechsel item varies.
    int16_t     filter_dep_id;       // 642 (8000), 101 (1000, 900),
                                     // 105 (700/79X, 600)
} nivona_stats_t;

// Returns the stat table for the currently active family (never NULL —
// falls back to 8000's table for unknown families).
const nivona_stats_t *nivona_stats_current(void);

// Family-by-key lookup.
const nivona_stats_t *nivona_stats_for_family(const char *family_key);

// True if HR id (200+selector) is a valid per-recipe counter on `s`.
// Selector range: 0..24 (mask width). Values outside the mask return
// false.
bool nivona_stats_has_recipe_counter(const nivona_stats_t *s, uint8_t selector);

#ifdef __cplusplus
}
#endif
