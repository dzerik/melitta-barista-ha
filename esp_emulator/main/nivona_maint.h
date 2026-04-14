// Nivona manipulation / maintenance orchestrator.
//
// Reads consumable + part state from nivona_consumables and decides
// which `manipulation` byte to feed into the HX status block at any
// given moment. Also handles HY confirms: soft prompts clear
// immediately; hard prompts (FILL_WATER / EMPTY_TRAYS / BU_REMOVED)
// stay raised until the underlying consumable is back within range.
//
// Phase D of the Nivona full-emulation roadmap
// (docs/NIVONA_RE_NOTES.md §Phase D).

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Re-evaluate all consumables / parts and push the strongest
// (highest-priority) manipulation into the FSM status block.
// Call after any state change: brew consumed a resource, user refilled
// a tank via CLI, HY confirm cleared a soft prompt.
//
// If all consumables are within range → manipulation = NONE.
void nivona_maint_reevaluate(void);

// HY confirm entry point. Called by dispatcher on CMD_CONFIRM.
// Returns true if the confirm cleared a prompt, false if the prompt
// is hard-locked by an unsatisfied consumable (HA shows an error and
// the user must physically refill).
bool nivona_maint_handle_confirm(void);

// Returns true if the current manipulation is "soft" — i.e. HY can
// clear it without requiring a consumable fix. Examples:
//   - FLUSH_REQUIRED (auto-flush on brew_unit cold start)
//   - MOVE_CUP_TO_FROTHER (user moved the cup)
bool nivona_maint_current_is_soft(void);

// Cold-start-after-boot sequencer. Simulates a real machine's
// post-power-on self check: if water low → FILL_WATER; if BU was
// removed → BU_REMOVED; else → FLUSH_REQUIRED (soft prompt, cleared
// by first HY). Call from main_app_init / nivona_fsm_init.
void nivona_maint_cold_start(void);

// Per-family manipulation allowlist — used by nivona_maint_reevaluate
// to suppress manipulations that the selected family cannot physically
// raise (e.g. FILL_POWDER on a 600-series without a powder lid).
// Declared here so nivona_families.c can provide per-family masks.
uint32_t nivona_maint_family_mask(const char *family_key);

#ifdef __cplusplus
}
#endif
