// Nivona family-specific tuning table.
//
// Phase A of the Nivona-emulation roadmap (see
// docs/NIVONA_RE_NOTES.md). Centralises per-family values that the FSM
// and the brew task need in order to emulate different Nivona machines
// convincingly enough for both Home Assistant and the official Nivona
// Android app to work against whichever family the CLI `family <key>`
// command has selected.
//
// Values extracted from decompiled EugsterMobileApp (v3.8.6) —
// MakeCoffee()/CheckDiscovered() switch on CoffeeMachineModel.

#pragma once

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Sub-process codes emitted during brew ramp. Must match SubProcess
// enum in the HA integration (const.py:113) and the values the real
// Nivona firmware reports in the HX sub_process field during brew.
typedef enum {
    NIVONA_SUB_IDLE     = 0,
    NIVONA_SUB_GRINDING = 1,
    NIVONA_SUB_COFFEE   = 2,
    NIVONA_SUB_STEAM    = 3,
    NIVONA_SUB_WATER    = 4,
    NIVONA_SUB_PREPARE  = 5,
} nivona_sub_process_t;

// Recipe category → brew ramp shape. Each stage executes in sequence,
// each gets an even share of the total brew time.
typedef enum {
    NIVONA_CAT_ESPRESSO,   // GRINDING → COFFEE                 (short)
    NIVONA_CAT_COFFEE,     // GRINDING → COFFEE                 (medium)
    NIVONA_CAT_AMERICANO,  // GRINDING → COFFEE → WATER
    NIVONA_CAT_MILK_DRINK, // GRINDING → COFFEE → STEAM         (cappuccino, latte, …)
    NIVONA_CAT_MILK_ONLY,  // STEAM                             (hot milk / foam)
    NIVONA_CAT_WATER,      // WATER                             (hot water)
    NIVONA_CAT_UNKNOWN,    // Unknown selector — reject with NACK
} nivona_recipe_category_t;

typedef struct {
    uint8_t                  selector;   // HE payload byte[3]
    const char              *name;       // Display
    nivona_recipe_category_t category;
} nivona_recipe_t;

typedef struct {
    const char *key;            // "600" / "700" / "79x" / … / "8000"
    const char *ble_name;       // Advertised local_name — bare serial
                                // with 5 trailing dashes. App takes
                                // Substring(0, 4) as model code.
    const char *model;          // Display model name (e.g. "NIVO 8107")

    // Phase A — HX FSM process codes
    int16_t process_ready;      // NIVO 8000 = 3, others = 8
    int16_t process_brewing;    // NIVO 8000 = 4, others = 11

    // Phase C-lite — brew payload scaling (populated but not yet
    // consumed; HW override handler will read it in a later slice).
    uint8_t fluid_scale;        // 900/1030/1040 = 10, others = 1
    uint8_t has_milk_system;    // 900/1030/1040/8000 = 1, others = 0

    // HE payload byte[1] expected "brew command mode" —
    // EugsterMobileApp.decompiled.cs:6463. Real machine rejects
    // HE with wrong mode.
    //   NIVO 8000 → 0x04
    //   all other → 0x0B (= 11; note this shares the brewing Process
    //                     code on non-8000 families by coincidence)
    uint8_t brew_command_mode;

    // Phase C-lite — per-family recipe table (selector → category).
    // NULL-terminated semantics: iterate up to recipe_count.
    const nivona_recipe_t *recipes;
    size_t                 recipe_count;
} nivona_family_t;

// All known Nivona families. Size via NIVONA_FAMILIES_COUNT.
extern const nivona_family_t NIVONA_FAMILIES[];
extern const size_t NIVONA_FAMILIES_COUNT;

// Returns the currently active family. Defaults to the "8000" entry
// at boot. Never NULL.
const nivona_family_t *nivona_family_current(void);

// Switches the active family by key. Returns 0 on success, non-zero
// if the key is unknown. Caller is responsible for pushing the new
// ble_name / DIS values; this call just updates the table pointer so
// the FSM and brew task read the right codes.
int nivona_family_set(const char *key);

// Convenience lookup (read-only). Returns NULL on miss.
const nivona_family_t *nivona_family_find(const char *key);

// Resolve an HE-selector byte to a recipe in the current family.
// Returns NULL if the selector is not known for this family — the
// HE handler should NACK in that case.
const nivona_recipe_t *nivona_family_recipe_by_selector(
    const nivona_family_t *fam, uint8_t selector);

#ifdef __cplusplus
}
#endif
