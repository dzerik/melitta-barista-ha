#include "nivona_families.h"

#include <string.h>

// Per-family table — single source of truth for the emulator.
//
// Process codes (process_ready / process_brewing) come from the
// decompiled Android app's MakeCoffee() switch:
//   NIVO 8000     → 3 / 4
//   All others    → 8 / 11
// See docs/NIVONA_RE_NOTES.md §Phase A.
//
// fluid_scale / has_milk_system populated for Phase C-lite — not yet
// consumed by the HE handler.

const nivona_family_t NIVONA_FAMILIES[] = {
    // key         ble_name              model         ready  brew   scale  milk
    { "600",       "6801000001-----",   "NICR 680",   8,     11,    1,     0 },
    { "700",       "7591000001-----",   "NICR 759",   8,     11,    1,     0 },
    { "79x",       "7951000001-----",   "NICR 795",   8,     11,    1,     0 },
    { "900",       "9301000001-----",   "NICR 930",   8,     11,    10,    1 },
    { "900-light", "9701000001-----",   "NICR 970",   8,     11,    10,    1 },
    { "1030",      "0301000001-----",   "NICR 1030",  8,     11,    10,    1 },
    { "1040",      "0401000001-----",   "NICR 1040",  8,     11,    10,    1 },
    { "8000",      "8107000001-----",   "NIVO 8107",  3,     4,     1,     1 },
};

const size_t NIVONA_FAMILIES_COUNT =
    sizeof(NIVONA_FAMILIES) / sizeof(NIVONA_FAMILIES[0]);

// Default: NIVO 8000 (matches the historical hardcoded FSM values
// and the app's default scan target). CLI `family <key>` overrides.
static const nivona_family_t *s_current = &NIVONA_FAMILIES[7];

const nivona_family_t *nivona_family_current(void) {
    return s_current;
}

const nivona_family_t *nivona_family_find(const char *key) {
    if (!key) return NULL;
    for (size_t i = 0; i < NIVONA_FAMILIES_COUNT; i++) {
        if (strcmp(key, NIVONA_FAMILIES[i].key) == 0) {
            return &NIVONA_FAMILIES[i];
        }
    }
    return NULL;
}

int nivona_family_set(const char *key) {
    const nivona_family_t *f = nivona_family_find(key);
    if (f == NULL) return -1;
    s_current = f;
    return 0;
}
