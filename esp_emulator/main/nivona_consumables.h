// Simulated consumables / wear state for the Nivona emulator.
//
// Each consumable is a percentage (0–100) that drifts over the machine's
// "lifetime" — brew_task consumes water / beans / tray space, user
// refills via CLI. Thresholds trigger `manipulation` byte changes in
// the HX status so Home Assistant and the Nivona Android app see
// realistic "fill water", "empty trays", "fill beans" prompts.
//
// Phase D of the Nivona full-emulation roadmap
// (docs/NIVONA_RE_NOTES.md §Phase D).

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Consumable identifiers. Keep the enum compact — each maps to a
// manipulation code defined in nivona_fsm.h.
typedef enum {
    NIVONA_CONSUM_WATER  = 0,   // 0 % = empty,   100 % = full
    NIVONA_CONSUM_BEANS  = 1,   // 0 % = empty
    NIVONA_CONSUM_TRAY   = 2,   // 0 % = empty,   100 % = full (needs emptying)
    NIVONA_CONSUM_FILTER = 3,   // 100 % = fresh, 0 % = needs replace
    NIVONA_CONSUM_COUNT,
} nivona_consumable_t;

// Mechanical parts — boolean present/absent (brew unit, drip tray).
typedef enum {
    NIVONA_PART_BREW_UNIT = 0,
    NIVONA_PART_TRAYS     = 1,
    NIVONA_PART_POWDER_LID = 2,
    NIVONA_PART_COUNT,
} nivona_part_t;

// Thresholds (percent). Tuned for "roughly 10 brews before refill"
// semantics at the default consumption rates — see nivona_consumables.c.
#define NIVONA_THR_WATER_LOW   10   // < this → FILL_WATER
#define NIVONA_THR_BEANS_LOW    5   // < this → FILL_BEANS
#define NIVONA_THR_TRAY_FULL   90   // > this → EMPTY_TRAYS
#define NIVONA_THR_FILTER_LOW   5   // < this → filter-replace soft prompt

void nivona_consumables_init(void);

// Reset to factory-full state (water = 100, beans = 100, tray = 0,
// filter = 100, all parts present).
void nivona_consumables_reset(void);

// Getters.
uint8_t  nivona_consumable_get(nivona_consumable_t c);
bool     nivona_part_get(nivona_part_t p);

// Setters — value clamped to 0..100.
void nivona_consumable_set(nivona_consumable_t c, uint8_t pct);
void nivona_part_set(nivona_part_t p, bool present);

// Adjust a consumable by delta (signed). Clamps result to 0..100.
void nivona_consumable_adjust(nivona_consumable_t c, int delta);

// Consumption hooks — call once per brew stage. delta is in percent.
// brew_task uses these; they're thin wrappers over _adjust so the
// consumption profile stays in one place for tuning.
void nivona_consumables_consume_water(uint8_t pct);
void nivona_consumables_consume_beans(uint8_t pct);
void nivona_consumables_fill_tray(uint8_t pct);

// Pretty-print the whole state (for the CLI `status tanks` command).
void nivona_consumables_dump(void);

// Human-readable name of a consumable / part (for CLI / logs).
const char *nivona_consumable_name(nivona_consumable_t c);
const char *nivona_part_name(nivona_part_t p);

#ifdef __cplusplus
}
#endif
