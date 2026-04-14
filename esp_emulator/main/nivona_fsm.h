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

typedef enum {
    MANIP_NONE           = 0,
    MANIP_WATER_EMPTY    = 1,
    MANIP_BEANS_EMPTY    = 2,
    MANIP_TRAY_FULL      = 3,
    MANIP_CLEAN          = 4,
    MANIP_DESCALE        = 5,
} nivona_manipulation_t;

typedef struct {
    int16_t process;
    int16_t sub_process;
    uint8_t info;
    uint8_t manipulation;
    int16_t progress;
} nivona_status_t;

void nivona_fsm_init(void);

// Read current status (thread-safe copy)
void nivona_fsm_get_status(nivona_status_t *out);

// Setters (used by CLI / dispatch / Phase 6 brew loop)
void nivona_fsm_set_process(int16_t process, int16_t sub_process);
void nivona_fsm_set_progress(int16_t progress);
void nivona_fsm_set_manipulation(uint8_t manip);
void nivona_fsm_set_info(uint8_t info);

// Serialize status to 8-byte HX payload (big-endian).
void nivona_fsm_pack_status(uint8_t out[8]);
