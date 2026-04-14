#include "nivona_brew.h"
#include "nivona_consumables.h"
#include "nivona_families.h"
#include "nivona_fsm.h"
#include "nivona_frame.h"
#include "nivona_maint.h"
#include "nivona_stats.h"
#include "nivona_store.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"

static const char *TAG = "nivona_brew";

static TaskHandle_t s_task = NULL;
static volatile bool s_active = false;
static volatile bool s_cancel = false;

// Send an unsolicited HX status notification so subscribers see progress.
static void push_status(void) {
    uint8_t payload[8];
    nivona_fsm_pack_status(payload);
    // Per NIVONA.md:238, responses don't include the session key prefix.
    nivona_frame_send("HX", payload, sizeof(payload),
                      /*include_key_prefix=*/false, /*encrypt=*/true);
}

// Per-category brew-ramp shape. Each entry is a sequence of stages;
// each stage declares which sub_process code to emit and what share
// of the total brew time it takes (weights summed across stages ≈ 1.0).
// Total wall-clock duration is `total_ms`.
typedef struct {
    nivona_sub_process_t sub;
    uint8_t              weight;   // arbitrary units; normalised at runtime
} brew_stage_t;

typedef struct {
    nivona_recipe_category_t category;
    uint32_t                 total_ms;
    const brew_stage_t      *stages;
    size_t                   stage_count;
} brew_ramp_t;

static const brew_stage_t S_ESPRESSO[]   = { {NIVONA_SUB_GRINDING, 3}, {NIVONA_SUB_COFFEE, 7} };
static const brew_stage_t S_COFFEE[]     = { {NIVONA_SUB_GRINDING, 3}, {NIVONA_SUB_COFFEE, 7} };
static const brew_stage_t S_AMERICANO[]  = { {NIVONA_SUB_GRINDING, 2}, {NIVONA_SUB_COFFEE, 5}, {NIVONA_SUB_WATER, 3} };
static const brew_stage_t S_MILK_DRINK[] = { {NIVONA_SUB_GRINDING, 2}, {NIVONA_SUB_COFFEE, 4}, {NIVONA_SUB_STEAM, 4} };
static const brew_stage_t S_MILK_ONLY[]  = { {NIVONA_SUB_STEAM, 10} };
static const brew_stage_t S_WATER[]      = { {NIVONA_SUB_WATER, 10} };

#define RAMP_COUNT(arr) (sizeof(arr) / sizeof((arr)[0]))

static const brew_ramp_t RAMPS[] = {
    { NIVONA_CAT_ESPRESSO,   20000, S_ESPRESSO,   RAMP_COUNT(S_ESPRESSO)   },
    { NIVONA_CAT_COFFEE,     28000, S_COFFEE,     RAMP_COUNT(S_COFFEE)     },
    { NIVONA_CAT_AMERICANO,  35000, S_AMERICANO,  RAMP_COUNT(S_AMERICANO)  },
    { NIVONA_CAT_MILK_DRINK, 45000, S_MILK_DRINK, RAMP_COUNT(S_MILK_DRINK) },
    { NIVONA_CAT_MILK_ONLY,  20000, S_MILK_ONLY,  RAMP_COUNT(S_MILK_ONLY)  },
    { NIVONA_CAT_WATER,      15000, S_WATER,      RAMP_COUNT(S_WATER)      },
};

static const brew_ramp_t *find_ramp(nivona_recipe_category_t cat) {
    for (size_t i = 0; i < RAMP_COUNT(RAMPS); i++) {
        if (RAMPS[i].category == cat) return &RAMPS[i];
    }
    return &RAMPS[1]; // fallback to "coffee"
}

// Brew-task argument (packs selector + resolved recipe pointer).
typedef struct {
    uint8_t                selector;
    const nivona_recipe_t *recipe; // may be NULL when only selector known
    bool                   two_cups;
} brew_arg_t;

static brew_arg_t s_arg;

static void brew_task(void *arg) {
    (void)arg; // args passed via s_arg to avoid intptr_t truncation

    // Snapshot current family's codes at brew start — if the CLI
    // switches family mid-brew the ramp still finishes consistently.
    const nivona_family_t *fam = nivona_family_current();
    const int16_t brew_code = fam->process_brewing;
    const int16_t ready_code = fam->process_ready;

    const nivona_recipe_t *recipe = s_arg.recipe;
    const char *name = recipe ? recipe->name : "?";
    nivona_recipe_category_t cat =
        recipe ? recipe->category : NIVONA_CAT_COFFEE;
    const brew_ramp_t *ramp = find_ramp(cat);

    uint32_t total_ms = ramp->total_ms;
    if (s_arg.two_cups) total_ms *= 2;

    ESP_LOGI(TAG, "brew start family=%s selector=%u recipe=%s cat=%d "
             "total=%ums stages=%u brew_code=%d",
             fam->key, s_arg.selector, name, (int)cat,
             (unsigned)total_ms, (unsigned)ramp->stage_count,
             brew_code);

    // Announce brewing state — the Nivona app re-reads HX ~650 ms after
    // HE and requires the family-specific brewing code (4 / 11).
    nivona_fsm_set_process(brew_code, (int16_t)s_arg.selector);
    nivona_fsm_set_progress(0);
    nivona_fsm_set_info(0);
    push_status();

    // Compute per-stage wall-clock budget from weights.
    uint32_t weight_sum = 0;
    for (size_t i = 0; i < ramp->stage_count; i++) {
        weight_sum += ramp->stages[i].weight;
    }
    if (weight_sum == 0) weight_sum = 1;

    uint32_t elapsed_ms = 0;
    for (size_t i = 0; i < ramp->stage_count && !s_cancel; i++) {
        const brew_stage_t *st = &ramp->stages[i];
        uint32_t stage_ms = (total_ms * st->weight) / weight_sum;

        // Consume resources for this stage. Rough per-stage budget:
        //   GRINDING → 3 % beans + fills tray by 2 % (grounds)
        //   COFFEE   → 3 % water per stage (espresso uses ~30 ml of
        //              a 1.8 L tank ≈ 1.7 %, round to 3 for a visible
        //              drift over ~20 brews)
        //   WATER    → 5 % water (americano top-up, hot water drink)
        //   STEAM    → 3 % water (milk drinks consume some steam water)
        //   PREPARE  → nothing
        switch (st->sub) {
            case NIVONA_SUB_GRINDING:
                nivona_consumables_consume_beans(3);
                nivona_consumables_fill_tray(2);
                break;
            case NIVONA_SUB_COFFEE:
                nivona_consumables_consume_water(3);
                break;
            case NIVONA_SUB_WATER:
                nivona_consumables_consume_water(5);
                break;
            case NIVONA_SUB_STEAM:
                nivona_consumables_consume_water(3);
                break;
            default:
                break;
        }

        // sub_process code reflects the current stage; HX readers
        // (HA's SubProcess enum, Android app UI) observe the change.
        nivona_fsm_set_process(brew_code, (int16_t)st->sub);
        ESP_LOGI(TAG, "  stage[%u/%u] sub=%d duration=%ums",
                 (unsigned)(i + 1), (unsigned)ramp->stage_count,
                 (int)st->sub, (unsigned)stage_ms);

        // Tick at 500 ms inside the stage, updating progress as a
        // percentage of the whole brew (so ramp 0→100 across all stages).
        const uint32_t TICK_MS = 500;
        uint32_t stage_elapsed = 0;
        while (stage_elapsed < stage_ms && !s_cancel) {
            uint32_t step = stage_ms - stage_elapsed;
            if (step > TICK_MS) step = TICK_MS;
            vTaskDelay(pdMS_TO_TICKS(step));
            stage_elapsed += step;
            elapsed_ms += step;
            int16_t pct = (int16_t)((elapsed_ms * 100) / total_ms);
            if (pct > 100) pct = 100;
            nivona_fsm_set_progress(pct);
            push_status();
        }
    }

    // Back to family-specific READY.
    nivona_fsm_set_process(ready_code, 0);
    nivona_fsm_set_progress(0);

    // Cup-counter tick — only on non-cancelled completion. Per-family
    // authoritative stat-ID tables from nivona_stats (ported from
    // StatisticsFactory.GetAvailableStatisticsFor* in
    // EugsterMobileApp.decompiled.cs:9146-9306). We gate every write
    // on `nivona_stats_has_recipe_counter` / `total_id != 0` so we
    // never cache an HR id the real machine doesn't expose for this
    // family. Writing bogus IDs is silently accepted by the emulator
    // but would create ghost stat sensors in any honest HA stats map.
    if (!s_cancel) {
        const nivona_stats_t *stats = nivona_stats_current();
        if (nivona_stats_has_recipe_counter(stats, s_arg.selector)) {
            int16_t sel_id = (int16_t)(200 + s_arg.selector);
            nivona_store_set_num(sel_id,
                nivona_store_get_num(sel_id) + 1);
            ESP_LOGI(TAG, "cup counter: selector %u → HR %d = %d",
                     s_arg.selector, (int)sel_id,
                     (int)nivona_store_get_num(sel_id));
        } else {
            ESP_LOGW(TAG, "cup counter: selector %u has no HR counter "
                     "on family %s (per StatisticsFactory) — skipped",
                     s_arg.selector,
                     fam ? fam->key : "?");
        }
        if (stats->total_id != 0) {
            nivona_store_set_num(stats->total_id,
                nivona_store_get_num(stats->total_id) + 1);
            ESP_LOGI(TAG, "total counter: HR %d = %d",
                     (int)stats->total_id,
                     (int)nivona_store_get_num(stats->total_id));
        }
        // Family-specific "via app" bump — app-verified HR id
        // (ProduktebezuegeUeberApp). Absent on 700/79X/600.
        if (stats->via_app_id != 0) {
            nivona_store_set_num(stats->via_app_id,
                nivona_store_get_num(stats->via_app_id) + 1);
        }

        // Maintenance gauge degradation per brew (600/610/620/640 are
        // universal across all 5 families per StatisticsFactory
        // maintenance lists). Rates are heuristic — the real-hw
        // degradation profile is firmware-side and not in decompile.
        //   filter    -1 % per brew    (warn < 10)
        //   BU clean  -1 % per 2 brews (warn < 20)
        //   descale   -1 % per 5 brews (warn < 20)
        int32_t wear_ticks = (stats->total_id != 0)
            ? nivona_store_get_num(stats->total_id)
            : (nivona_store_get_num(610) == 0 ? 1 : 0); // fallback edge
        // Use a monotonic local counter if the family has no total:
        if (stats->total_id == 0) {
            static int32_t s_local_wear_tick = 0;
            wear_ticks = ++s_local_wear_tick;
        }

        int32_t filter_pct = nivona_store_get_num(640);
        if (filter_pct > 0) nivona_store_set_num(640, filter_pct - 1);
        if (nivona_store_get_num(640) < 10) nivona_store_set_num(641, 1);

        if ((wear_ticks & 1) == 0) {
            int32_t bu = nivona_store_get_num(610);
            if (bu > 0) nivona_store_set_num(610, bu - 1);
            if (nivona_store_get_num(610) < 20) nivona_store_set_num(611, 1);
        }
        if ((wear_ticks % 5) == 0) {
            int32_t ds = nivona_store_get_num(600);
            if (ds > 0) nivona_store_set_num(600, ds - 1);
            if (nivona_store_get_num(600) < 20) nivona_store_set_num(601, 1);
        }
    }

    // After every brew the maintenance orchestrator re-checks all
    // consumables. If the water tank just dropped below 10 %, a
    // FILL_WATER prompt surfaces in the next HX status — HA raises
    // its awaiting_confirmation binary sensor and the user has to
    // refill (CLI `fix water` / physical refill on a real machine).
    nivona_maint_reevaluate();
    push_status();

    s_active = false;
    s_cancel = false;
    s_task = NULL;
    ESP_LOGI(TAG, "brew done");
    vTaskDelete(NULL);
}

void nivona_brew_init(void) { /* nothing yet */ }

bool nivona_brew_start(int16_t process_value, bool two_cups) {
    if (s_active) {
        ESP_LOGW(TAG, "brew already active, rejecting");
        return false;
    }
    // Resolve selector → recipe descriptor in the current family.
    // process_value carries the selector byte from HE payload[3].
    const nivona_family_t *fam = nivona_family_current();
    uint8_t sel = (uint8_t)(process_value & 0xFF);
    const nivona_recipe_t *recipe = nivona_family_recipe_by_selector(fam, sel);
    if (recipe == NULL) {
        ESP_LOGW(TAG, "unknown selector %u on family %s — rejecting brew",
                 sel, fam->key);
        return false;
    }
    // Refuse brew while a hard prompt is active — a real machine
    // would reject HE with its own error. Soft prompts (FLUSH /
    // MOVE_CUP) are fine because the brew itself flushes them.
    nivona_status_t status;
    nivona_fsm_get_status(&status);
    if (status.manipulation != MANIP_NONE &&
        !nivona_maint_current_is_soft()) {
        ESP_LOGW(TAG, "brew rejected: hard prompt manip=%u active",
                 status.manipulation);
        return false;
    }

    s_arg.selector = sel;
    s_arg.recipe = recipe;
    s_arg.two_cups = two_cups;

    s_active = true;
    s_cancel = false;
    xTaskCreate(brew_task, "nivona_brew", 4096,
                NULL, 5, &s_task);
    return true;
}

void nivona_brew_cancel(void) {
    if (!s_active) return;
    s_cancel = true;
    ESP_LOGI(TAG, "brew cancel requested");
}

bool nivona_brew_active(void) { return s_active; }
