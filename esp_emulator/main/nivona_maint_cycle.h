// Long-running maintenance cycles — Phase E of the Nivona full-
// emulation roadmap. Descale / easy-clean / intensive-clean /
// filter-insert / filter-replace / filter-remove / evaporating.
//
// Each cycle is a multi-stage state machine that takes seconds to
// minutes to run and matches what a real machine would put the user
// through — staged process codes, manipulation prompts, progress,
// and on completion a corresponding stat counter tick (descaling at
// HR id 220, filter_changes at 219, rinse_cycles at 216, etc.).
//
// The cycle codes used below match the Melitta MachineProcess IntEnum
// (const.py:99) because the upstream decompile of the Nivona app did
// not surface distinct cleaning-cycle codes — real Nivona hardware
// may report different values (see docs/NIVONA_RE_NOTES.md §Phase E
// open questions). We use Melitta codes as the best-available default;
// the HA integration's NivonaProfile.parse_status will need a matching
// per-family mapping once community traces arrive.

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    NIVONA_CYCLE_DESCALE         = 10, // PROC_DESCALING
    NIVONA_CYCLE_EASY_CLEAN      = 17, // PROC_EASY_CLEAN
    NIVONA_CYCLE_INTENSIVE_CLEAN = 19, // PROC_INTENSIVE_CLEAN
    NIVONA_CYCLE_FILTER_INSERT   = 11, // PROC_FILTER_INSERT
    NIVONA_CYCLE_FILTER_REPLACE  = 12, // PROC_FILTER_REPLACE
    NIVONA_CYCLE_FILTER_REMOVE   = 13, // PROC_FILTER_REMOVE
    NIVONA_CYCLE_EVAPORATING     = 20, // PROC_EVAPORATING
    NIVONA_CYCLE_GENERIC_CLEAN   = 9,  // PROC_CLEANING (rinse)
} nivona_cycle_kind_t;

void nivona_maint_cycle_init(void);

// Start a cycle. Returns false if one is already running or a brew
// is active. The task runs asynchronously; HX notifications are
// pushed through the brew_status channel.
bool nivona_maint_cycle_start(nivona_cycle_kind_t kind);

// Cancel the currently-running cycle (like the brew cancel). Some
// cycles (DESCALE) refuse mid-way on a real machine — we just cancel
// for emulator flexibility.
void nivona_maint_cycle_cancel(void);

bool nivona_maint_cycle_active(void);

// Map a CLI keyword ("descale" / "easy_clean" / …) to the enum;
// returns -1 on unknown.
int nivona_maint_cycle_from_name(const char *name);

// Reverse: enum → canonical name (for logs).
const char *nivona_maint_cycle_name(nivona_cycle_kind_t k);

#ifdef __cplusplus
}
#endif
