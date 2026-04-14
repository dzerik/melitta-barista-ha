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

// Cycle codes are Melitta-derived placeholders. The Nivona app does
// not branch on cleaning-cycle Process values (only 0/11/20 on Message
// and the 3/4/8/11 ready/brewing codes), so these never reach any
// wire consumer that would tell us they're wrong — but they are also
// NOT verified against Nivona firmware. See docs/NIVONA_RE_NOTES.md
// §Phase E for the follow-up BLE-capture task needed to resolve.
// Value 11 (FILTER_INSERT) collides with Nivona's brewing code for
// non-8000 families and must never be emitted in that context.
typedef enum {
    NIVONA_CYCLE_DESCALE         = 10, // MELITTA_PROC_DESCALING (TBD Nivona)
    NIVONA_CYCLE_EASY_CLEAN      = 17, // MELITTA_PROC_EASY_CLEAN (TBD Nivona)
    NIVONA_CYCLE_INTENSIVE_CLEAN = 19, // MELITTA_PROC_INTENSIVE_CLEAN (TBD Nivona)
    NIVONA_CYCLE_FILTER_INSERT   = 11, // ⚠ collides with Nivona brewing=11
    NIVONA_CYCLE_FILTER_REPLACE  = 12, // MELITTA_PROC_FILTER_REPLACE (TBD Nivona)
    NIVONA_CYCLE_FILTER_REMOVE   = 13, // MELITTA_PROC_FILTER_REMOVE (TBD Nivona)
    NIVONA_CYCLE_EVAPORATING     = 20, // ⚠ value 20 also used as HX Message
    NIVONA_CYCLE_GENERIC_CLEAN   = 9,  // MELITTA_PROC_CLEANING (TBD Nivona)
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
