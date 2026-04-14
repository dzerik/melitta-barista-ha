#include "nivona_maint.h"

#include <string.h>

#include "nivona_consumables.h"
#include "nivona_families.h"
#include "nivona_fsm.h"

#include "esp_log.h"

static const char *TAG = "nivona_maint";

// Convert a manipulation code into a bit for the family allowlist.
#define BIT(m) (1u << (m))

// Per-family bitmask of manipulations the family can physically raise.
// Derived from hardware differences (powder lid presence, milk system,
// tray geometry). Conservative defaults — will be refined once real
// machine traces arrive via community reports.
static uint32_t mask_common(void) {
    return BIT(MANIP_NONE)
         | BIT(MANIP_BU_REMOVED)
         | BIT(MANIP_TRAYS_MISSING)
         | BIT(MANIP_EMPTY_TRAYS)
         | BIT(MANIP_FILL_WATER)
         | BIT(MANIP_FLUSH_REQUIRED);
}

uint32_t nivona_maint_family_mask(const char *family_key) {
    uint32_t m = mask_common();
    if (family_key == NULL) return m;

    // Pro-model families (900/1030/1040/8000) have milk systems —
    // they can prompt MOVE_CUP_TO_FROTHER during milk drinks.
    const nivona_family_t *fam = nivona_family_find(family_key);
    if (fam != NULL && fam->has_milk_system) {
        m |= BIT(MANIP_MOVE_CUP);
    }
    // 1030 / 1040 typically have a ground-coffee powder chute
    // (for decaf shots). 600/700/79x do not — leave those clear.
    if (family_key != NULL &&
        (strcmp(family_key, "1030") == 0 ||
         strcmp(family_key, "1040") == 0 ||
         strcmp(family_key, "8000") == 0)) {
        m |= BIT(MANIP_CLOSE_POWDER_LID) | BIT(MANIP_FILL_POWDER);
    }
    return m;
}

// Priority ordering when multiple conditions are true. Roughly:
//   parts missing  > tank empty  > tray full  > soft prompts
// The emulator returns only the highest-priority prompt at any time;
// once that's resolved the next one surfaces.
static uint8_t evaluate(uint32_t allowed_mask) {
    // Parts first — you can't brew without the brew unit or trays.
    if (!nivona_part_get(NIVONA_PART_BREW_UNIT) &&
        (allowed_mask & BIT(MANIP_BU_REMOVED))) {
        return MANIP_BU_REMOVED;
    }
    if (!nivona_part_get(NIVONA_PART_TRAYS) &&
        (allowed_mask & BIT(MANIP_TRAYS_MISSING))) {
        return MANIP_TRAYS_MISSING;
    }
    if (!nivona_part_get(NIVONA_PART_POWDER_LID) &&
        (allowed_mask & BIT(MANIP_CLOSE_POWDER_LID))) {
        return MANIP_CLOSE_POWDER_LID;
    }
    // Tanks.
    if (nivona_consumable_get(NIVONA_CONSUM_WATER) < NIVONA_THR_WATER_LOW &&
        (allowed_mask & BIT(MANIP_FILL_WATER))) {
        return MANIP_FILL_WATER;
    }
    if (nivona_consumable_get(NIVONA_CONSUM_TRAY) > NIVONA_THR_TRAY_FULL &&
        (allowed_mask & BIT(MANIP_EMPTY_TRAYS))) {
        return MANIP_EMPTY_TRAYS;
    }
    // Beans low is not in the canonical Manipulation enum — HA surfaces
    // it via info-message byte instead. Leave as TODO when we wire info.
    return MANIP_NONE;
}

void nivona_maint_reevaluate(void) {
    const nivona_family_t *fam = nivona_family_current();
    uint32_t mask = nivona_maint_family_mask(fam ? fam->key : NULL);
    uint8_t manip = evaluate(mask);
    nivona_fsm_set_manipulation(manip);
    if (manip != MANIP_NONE) {
        ESP_LOGI(TAG, "raising manipulation=%u (family=%s)",
                 manip, fam ? fam->key : "?");
    }
}

bool nivona_maint_current_is_soft(void) {
    // Soft prompts are those the user can clear by pressing "ok" on
    // the machine (or sending HY) without touching hardware.
    // We expose the FLUSH / MOVE_CUP codes — rest are hardware-bound.
    nivona_status_t s;
    nivona_fsm_get_status(&s);
    uint8_t m = s.manipulation;
    return m == MANIP_FLUSH_REQUIRED || m == MANIP_MOVE_CUP;
}

bool nivona_maint_handle_confirm(void) {
    nivona_status_t s;
    nivona_fsm_get_status(&s);
    uint8_t m = s.manipulation;
    if (m == MANIP_NONE) {
        ESP_LOGI(TAG, "HY: no prompt active");
        return true; // nothing to confirm, still a valid HY
    }
    if (nivona_maint_current_is_soft()) {
        // Clear the soft prompt outright. Re-evaluate in case a
        // hard prompt is underneath.
        ESP_LOGI(TAG, "HY: clearing soft prompt %u", m);
        nivona_fsm_set_manipulation(MANIP_NONE);
        nivona_maint_reevaluate();
        return true;
    }
    // Hard prompt — HY noise. Re-evaluate (in case user *did* fix
    // the tank via CLI between prompt raise and HY).
    ESP_LOGW(TAG, "HY on hard prompt %u — re-evaluating", m);
    nivona_maint_reevaluate();
    nivona_fsm_get_status(&s);
    return s.manipulation == MANIP_NONE;
}

void nivona_maint_cold_start(void) {
    // Real machines run a cold-start flush to clear residual water
    // from the thermoblock. Simulate by raising FLUSH_REQUIRED as a
    // soft prompt — cleared by the first HY, or automatically after
    // a few seconds if nivona_maint_reevaluate is called and the
    // FSM logic decides nothing else needs attention.
    //
    // For now we only raise it if nothing hard is pending.
    nivona_maint_reevaluate();
    nivona_status_t s;
    nivona_fsm_get_status(&s);
    if (s.manipulation == MANIP_NONE) {
        ESP_LOGI(TAG, "cold-start: raising FLUSH_REQUIRED (soft)");
        nivona_fsm_set_manipulation(MANIP_FLUSH_REQUIRED);
    }
}
