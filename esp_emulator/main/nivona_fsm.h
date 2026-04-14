#pragma once

#include <stdint.h>
#include <stdbool.h>

// Machine process states.
//
// NOTE (audit V2 — Focus 1): The "ready" and "brewing" codes are
// FAMILY-SPECIFIC on Nivona, not universal as this enum implies.
// Authoritative values come from `nivona_families.c` at runtime:
//   NIVO 8000:  ready = 3,  brewing = 4
//   all other:  ready = 8,  brewing = 11
// (EugsterMobileApp.Droid.decompiled.cs:25934-25935.)
//
// The MELITTA_* constants below are kept only for debugging CLI and
// for long-running maintenance cycles (descale / clean / filter_*)
// where the Nivona firmware's actual process code values are UNKNOWN
// from app decompile (firmware-internal). The real Nivona app does
// not inspect these codes — it only branches on Process == one of
// the ready/brewing values above and on Message == {0,11,20}.
// DO NOT use these for the "ready" or "brewing" transitions — use
// nivona_family_current()->process_ready / process_brewing.
typedef enum {
    MELITTA_PROC_CLEANING        = 9,    // TBD for Nivona
    MELITTA_PROC_DESCALING       = 10,   // TBD for Nivona
    MELITTA_PROC_FILTER_INSERT   = 11,   // collides with Nivona brewing=11
    MELITTA_PROC_FILTER_REPLACE  = 12,   // TBD for Nivona
    MELITTA_PROC_FILTER_REMOVE   = 13,   // TBD for Nivona
    MELITTA_PROC_SWITCH_OFF      = 16,   // TBD for Nivona
    MELITTA_PROC_EASY_CLEAN      = 17,   // TBD for Nivona
    MELITTA_PROC_INTENSIVE_CLEAN = 19,   // TBD for Nivona
    MELITTA_PROC_EVAPORATING     = 20,   // collides with Nivona Message=20
    MELITTA_PROC_BUSY            = 99,
} nivona_melitta_process_t;

// HX Message byte — values 0/11/20 are the ONLY ones the Nivona
// Android app branches on (EugsterMobileApp.Droid.decompiled.cs:906,
// 26082-26138). The 1..6 values here come from the Melitta
// Manipulation IntEnum (const.py:141); real Nivona firmware may use
// different values for {BU_REMOVED, TRAYS_MISSING, EMPTY_TRAYS,
// FILL_WATER, CLOSE_POWDER_LID, FILL_POWDER} — audit V2 Focus 3
// still-TBD. Keep the names because HA uses them; the numeric values
// for 1..6 are an emulator convention that matches HA's parser.
typedef enum {
    MANIP_NONE              = 0,   // app-verified
    MANIP_BU_REMOVED        = 1,   // Melitta-derived, Nivona TBD
    MANIP_TRAYS_MISSING     = 2,   // Melitta-derived, Nivona TBD
    MANIP_EMPTY_TRAYS       = 3,   // Melitta-derived, Nivona TBD
    MANIP_FILL_WATER        = 4,   // Melitta-derived, Nivona TBD
    MANIP_CLOSE_POWDER_LID  = 5,   // Melitta-derived, Nivona TBD
    MANIP_FILL_POWDER       = 6,   // Melitta-derived, Nivona TBD
    MANIP_MOVE_CUP          = 11,  // app-verified (Droid:26082)
    MANIP_FLUSH_REQUIRED    = 20,  // app-verified (Droid:906)
} nivona_manipulation_t;

typedef struct {
    int16_t process;
    int16_t sub_process;
    uint8_t info;
    uint8_t manipulation;
    int16_t progress;
} nivona_status_t;

void nivona_fsm_init(void);

// Re-apply the current family's READY codes to the status block —
// called after a runtime `family <key>` CLI switch so the next HX
// read reflects the new family's idle state without needing reboot.
void nivona_fsm_reset_to_ready(void);

// Read current status (thread-safe copy)
void nivona_fsm_get_status(nivona_status_t *out);

// Setters (used by CLI / dispatch / Phase 6 brew loop)
void nivona_fsm_set_process(int16_t process, int16_t sub_process);
void nivona_fsm_set_progress(int16_t progress);
void nivona_fsm_set_manipulation(uint8_t manip);
void nivona_fsm_set_info(uint8_t info);

// Serialize status to 8-byte HX payload (big-endian).
void nivona_fsm_pack_status(uint8_t out[8]);
