#pragma once

#include <stdint.h>
#include <stdbool.h>

// Machine process states (matches MachineProcess in const.py).
typedef enum {
    PROC_READY           = 2,
    PROC_PRODUCT         = 4,
    PROC_CLEANING        = 9,
    PROC_DESCALING       = 10,
    PROC_FILTER_INSERT   = 11,
    PROC_FILTER_REPLACE  = 12,
    PROC_FILTER_REMOVE   = 13,
    PROC_SWITCH_OFF      = 16,
    PROC_EASY_CLEAN      = 17,
    PROC_INTENSIVE_CLEAN = 19,
    PROC_EVAPORATING     = 20,
    PROC_BUSY            = 99,
} nivona_process_t;

// Must match Manipulation IntEnum in custom_components/melitta_barista/
// const.py (line 141). The emulator's HX manipulation byte is parsed
// against this exact enum by MachineStatus.from_payload; any drift
// between sides makes prompt entities unavailable / "unknown".
typedef enum {
    MANIP_NONE              = 0,
    MANIP_BU_REMOVED        = 1,
    MANIP_TRAYS_MISSING     = 2,
    MANIP_EMPTY_TRAYS       = 3,
    MANIP_FILL_WATER        = 4,
    MANIP_CLOSE_POWDER_LID  = 5,
    MANIP_FILL_POWDER       = 6,
    MANIP_MOVE_CUP          = 11,
    MANIP_FLUSH_REQUIRED    = 20,
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
