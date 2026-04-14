#include "nivona_stats.h"

#include <string.h>

#include "esp_log.h"

static const char *TAG = "nivona_stats";

// Helper: `BIT(n)` for recipe_id_mask where bit N = HR id (200+N).
#define R(n) (1u << (n))

// --- 8000 family (NIVO 8000) ---
// EugsterMobileApp.decompiled.cs:9146-9180
// Recipe: 200..210, 213
static const nivona_stats_t STATS_8000 = {
    .family_key      = "8000",
    .recipe_id_mask  = R(0) | R(1) | R(2) | R(3) | R(4) | R(5) | R(6) | R(7)
                       | R(8) | R(9) | R(10) | R(13),
    .total_id        = 213,
    .clean_coffee_id = 214,
    .clean_frother_id= 215,
    .rinse_id        = 216,
    .rinse_frother_id= 0,
    .rinse_filter_id = 0,
    .filter_change_id= 219,
    .descale_id      = 220,
    .via_app_id      = 221,
    .pot_id          = 0,
    .filter_dep_id   = 642,
};

// --- 1000 family (NICR 1030, NICR 1040) ---
// EugsterMobileApp.decompiled.cs:9182-9218
// Recipe IDs: 200-214, 215 (Total). 1040 variant skips 207 — tracked
// at lookup time via a conditional mask patch.
static const nivona_stats_t STATS_1000 = {
    .family_key      = "1000",
    .recipe_id_mask  = R(0) | R(1) | R(2) | R(3) | R(4) | R(5) | R(6) | R(7)
                       | R(8) | R(9) | R(11) | R(12) | R(13) | R(14),
    .total_id        = 215,
    .clean_coffee_id = 216,
    .clean_frother_id= 217,
    .rinse_id        = 218,
    .rinse_frother_id= 219,
    .rinse_filter_id = 220,
    .filter_change_id= 221,
    .descale_id      = 222,
    .via_app_id      = 223,
    .pot_id          = 224,
    .filter_dep_id   = 101,
};

// --- 700 / 79X family (NICR 700 series) ---
// EugsterMobileApp.decompiled.cs:9220-9241
// Recipe: 200-208 (79X: selector 4 absent; 201 label is "Kaffee" for
// 79X, "Creme" for 700). Counters 213+ absent on this family.
static const nivona_stats_t STATS_700 = {
    .family_key      = "700",
    .recipe_id_mask  = R(0) | R(1) | R(2) | R(3) | R(4) | R(5) | R(6) | R(7) | R(8),
    .total_id        = 0,
    .clean_coffee_id = 0,
    .clean_frother_id= 0,
    .rinse_id        = 0,
    .rinse_frother_id= 0,
    .rinse_filter_id = 0,
    .filter_change_id= 0,
    .descale_id      = 0,
    .via_app_id      = 0,
    .pot_id          = 0,
    .filter_dep_id   = 105,
};

// 79X variant: same as 700 but selector 4 (Cappuccino) is absent.
static const nivona_stats_t STATS_79X = {
    .family_key      = "79x",
    .recipe_id_mask  = R(0) | R(1) | R(2) | R(3) |       R(5) | R(6) | R(7) | R(8),
    .total_id        = 0,
    .clean_coffee_id = 0,
    .clean_frother_id= 0,
    .rinse_id        = 0,
    .rinse_frother_id= 0,
    .rinse_filter_id = 0,
    .filter_change_id= 0,
    .descale_id      = 0,
    .via_app_id      = 0,
    .pot_id          = 0,
    .filter_dep_id   = 105,
};

// --- 600 family (NICR 600 series) ---
// EugsterMobileApp.decompiled.cs:9243-9266
// Recipe: 200, 201, 203, 204, 206, 207, 208 (gaps at 202 and 205).
// Counters 213+ absent.
static const nivona_stats_t STATS_600 = {
    .family_key      = "600",
    .recipe_id_mask  = R(0) | R(1) |       R(3) | R(4) |       R(6) | R(7) | R(8),
    .total_id        = 0,
    .clean_coffee_id = 0,
    .clean_frother_id= 0,
    .rinse_id        = 0,
    .rinse_frother_id= 0,
    .rinse_filter_id = 0,
    .filter_change_id= 0,
    .descale_id      = 0,
    .via_app_id      = 0,
    .pot_id          = 0,
    .filter_dep_id   = 105,
};

// --- 900 / 900-Light family ---
// EugsterMobileApp.decompiled.cs:9268-9306
// Recipe: 200-212 (+213 total).
static const nivona_stats_t STATS_900 = {
    .family_key      = "900",
    .recipe_id_mask  = R(0) | R(1) | R(2) | R(3) | R(4) | R(5) | R(6) | R(7)
                       | R(8) | R(9) | R(10) | R(11) | R(12) | R(13),
    .total_id        = 213,
    .clean_coffee_id = 214,
    .clean_frother_id= 215,
    .rinse_id        = 216,
    .rinse_frother_id= 217,
    .rinse_filter_id = 218,
    .filter_change_id= 219,
    .descale_id      = 220,
    .via_app_id      = 221,
    .pot_id          = 0,
    .filter_dep_id   = 101,
};

// Emulator family key → app's "StatisticsFactory family" mapping.
static const nivona_stats_t *resolve(const char *family_key) {
    if (family_key == NULL) return &STATS_8000;
    if (!strcmp(family_key, "8000"))       return &STATS_8000;
    if (!strcmp(family_key, "1030"))       return &STATS_1000;
    if (!strcmp(family_key, "1040")) {
        // 1040 lacks selector 7 (HeisseMilch) — build a one-off copy
        // of STATS_1000 with that bit cleared. To keep state constant
        // we point at a static override below.
        static nivona_stats_t STATS_1040;
        static bool init_done = false;
        if (!init_done) {
            STATS_1040 = STATS_1000;
            STATS_1040.family_key = "1040";
            STATS_1040.recipe_id_mask &= ~R(7);
            init_done = true;
        }
        return &STATS_1040;
    }
    if (!strcmp(family_key, "900"))        return &STATS_900;
    if (!strcmp(family_key, "900-light"))  return &STATS_900;
    if (!strcmp(family_key, "79x"))        return &STATS_79X;
    if (!strcmp(family_key, "700"))        return &STATS_700;
    if (!strcmp(family_key, "600"))        return &STATS_600;
    ESP_LOGW(TAG, "unknown family key '%s' — falling back to 8000 stats",
             family_key);
    return &STATS_8000;
}

const nivona_stats_t *nivona_stats_for_family(const char *family_key) {
    return resolve(family_key);
}

const nivona_stats_t *nivona_stats_current(void) {
    const nivona_family_t *fam = nivona_family_current();
    return resolve(fam ? fam->key : NULL);
}

bool nivona_stats_has_recipe_counter(const nivona_stats_t *s, uint8_t selector) {
    if (s == NULL) return false;
    if (selector >= 25) return false;
    return (s->recipe_id_mask & R(selector)) != 0;
}
