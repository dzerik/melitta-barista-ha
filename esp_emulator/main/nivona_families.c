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
// Recipe tables mirror custom_components/melitta_barista/brands/nivona.py
// (Python source of truth for HA-side). Selectors are the HE payload
// byte[3] values the app sends. Categories drive the brew ramp shape
// in nivona_brew_task (Phase C-lite).

// ---- Per-family recipe tables (see brands/nivona.py) ----

static const nivona_recipe_t RECIPES_600[] = {
    { 0, "Espresso",    NIVONA_CAT_ESPRESSO   },
    { 1, "Coffee",      NIVONA_CAT_COFFEE     },
    { 2, "Americano",   NIVONA_CAT_AMERICANO  },
    { 3, "Cappuccino",  NIVONA_CAT_MILK_DRINK },
    { 4, "Frothy Milk", NIVONA_CAT_MILK_ONLY  },
    { 5, "Hot Water",   NIVONA_CAT_WATER      },
};

static const nivona_recipe_t RECIPES_700[] = {
    { 0, "Espresso",        NIVONA_CAT_ESPRESSO   },
    { 1, "Cream",           NIVONA_CAT_COFFEE     },
    { 2, "Lungo",           NIVONA_CAT_COFFEE     },
    { 3, "Americano",       NIVONA_CAT_AMERICANO  },
    { 4, "Cappuccino",      NIVONA_CAT_MILK_DRINK },
    { 5, "Latte Macchiato", NIVONA_CAT_MILK_DRINK },
    { 6, "Milk",            NIVONA_CAT_MILK_ONLY  },
    { 7, "Hot Water",       NIVONA_CAT_WATER      },
};

static const nivona_recipe_t RECIPES_79X[] = {
    { 0, "Espresso",        NIVONA_CAT_ESPRESSO   },
    { 1, "Coffee",          NIVONA_CAT_COFFEE     },
    { 2, "Americano",       NIVONA_CAT_AMERICANO  },
    { 3, "Cappuccino",      NIVONA_CAT_MILK_DRINK },
    // NB: selector 4 is deliberately absent in the upstream table.
    { 5, "Latte Macchiato", NIVONA_CAT_MILK_DRINK },
    { 6, "Milk",            NIVONA_CAT_MILK_ONLY  },
    { 7, "Hot Water",       NIVONA_CAT_WATER      },
};

static const nivona_recipe_t RECIPES_900[] = {
    { 0, "Espresso",        NIVONA_CAT_ESPRESSO   },
    { 1, "Coffee",          NIVONA_CAT_COFFEE     },
    { 2, "Americano",       NIVONA_CAT_AMERICANO  },
    { 3, "Cappuccino",      NIVONA_CAT_MILK_DRINK },
    { 4, "Caffè Latte",     NIVONA_CAT_MILK_DRINK },
    { 5, "Latte Macchiato", NIVONA_CAT_MILK_DRINK },
    { 6, "Hot Milk",        NIVONA_CAT_MILK_ONLY  },
    { 7, "Hot Water",       NIVONA_CAT_WATER      },
};

static const nivona_recipe_t RECIPES_1030[] = {
    { 0, "Espresso",        NIVONA_CAT_ESPRESSO   },
    { 1, "Coffee",          NIVONA_CAT_COFFEE     },
    { 2, "Americano",       NIVONA_CAT_AMERICANO  },
    { 3, "Cappuccino",      NIVONA_CAT_MILK_DRINK },
    { 4, "Caffè Latte",     NIVONA_CAT_MILK_DRINK },
    { 5, "Latte Macchiato", NIVONA_CAT_MILK_DRINK },
    { 6, "Hot Water",       NIVONA_CAT_WATER      },
    { 7, "Warm Milk",       NIVONA_CAT_MILK_ONLY  },
    { 8, "Hot Milk",        NIVONA_CAT_MILK_ONLY  },
    { 9, "Frothy Milk",     NIVONA_CAT_MILK_ONLY  },
};

static const nivona_recipe_t RECIPES_1040[] = {
    { 0, "Espresso",        NIVONA_CAT_ESPRESSO   },
    { 1, "Coffee",          NIVONA_CAT_COFFEE     },
    { 2, "Americano",       NIVONA_CAT_AMERICANO  },
    { 3, "Cappuccino",      NIVONA_CAT_MILK_DRINK },
    { 4, "Caffè Latte",     NIVONA_CAT_MILK_DRINK },
    { 5, "Latte Macchiato", NIVONA_CAT_MILK_DRINK },
    { 6, "Hot Water",       NIVONA_CAT_WATER      },
    { 7, "Warm Milk",       NIVONA_CAT_MILK_ONLY  },
    { 8, "Frothy Milk",     NIVONA_CAT_MILK_ONLY  },
};

static const nivona_recipe_t RECIPES_8000[] = {
    { 0, "Espresso",        NIVONA_CAT_ESPRESSO   },
    { 1, "Coffee",          NIVONA_CAT_COFFEE     },
    { 2, "Americano",       NIVONA_CAT_AMERICANO  },
    { 3, "Cappuccino",      NIVONA_CAT_MILK_DRINK },
    { 4, "Caffè Latte",     NIVONA_CAT_MILK_DRINK },
    { 5, "Latte Macchiato", NIVONA_CAT_MILK_DRINK },
    { 6, "Milk",            NIVONA_CAT_MILK_ONLY  },
    { 7, "Hot Water",       NIVONA_CAT_WATER      },
};

#define COUNT(arr) (sizeof(arr) / sizeof((arr)[0]))

const nivona_family_t NIVONA_FAMILIES[] = {
    // key         ble_name              model         ready  brew   scale  milk  recipes           n
    { "600",       "6801000001-----",   "NICR 680",   8,     11,    1,     0,    RECIPES_600,      COUNT(RECIPES_600)  },
    { "700",       "7591000001-----",   "NICR 759",   8,     11,    1,     0,    RECIPES_700,      COUNT(RECIPES_700)  },
    { "79x",       "7951000001-----",   "NICR 795",   8,     11,    1,     0,    RECIPES_79X,      COUNT(RECIPES_79X)  },
    { "900",       "9301000001-----",   "NICR 930",   8,     11,    10,    1,    RECIPES_900,      COUNT(RECIPES_900)  },
    { "900-light", "9701000001-----",   "NICR 970",   8,     11,    10,    1,    RECIPES_900,      COUNT(RECIPES_900)  },
    { "1030",      "0301000001-----",   "NICR 1030",  8,     11,    10,    1,    RECIPES_1030,     COUNT(RECIPES_1030) },
    { "1040",      "0401000001-----",   "NICR 1040",  8,     11,    10,    1,    RECIPES_1040,     COUNT(RECIPES_1040) },
    { "8000",      "8107000001-----",   "NIVO 8107",  3,     4,     1,     1,    RECIPES_8000,     COUNT(RECIPES_8000) },
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

const nivona_recipe_t *nivona_family_recipe_by_selector(
    const nivona_family_t *fam, uint8_t selector) {
    if (fam == NULL || fam->recipes == NULL) return NULL;
    for (size_t i = 0; i < fam->recipe_count; i++) {
        if (fam->recipes[i].selector == selector) return &fam->recipes[i];
    }
    return NULL;
}
