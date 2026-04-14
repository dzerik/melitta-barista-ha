#include "nivona_brew.h"
#include "nivona_families.h"
#include "nivona_fsm.h"
#include "nivona_frame.h"

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

static void brew_task(void *arg) {
    int16_t pv = (int16_t)(intptr_t)arg;
    // Snapshot current family's codes at brew start — if the CLI
    // switches family mid-brew the ramp still finishes consistently.
    const nivona_family_t *fam = nivona_family_current();
    const int16_t brew_code = fam->process_brewing;
    const int16_t ready_code = fam->process_ready;
    ESP_LOGI(TAG, "brew start family=%s pv=%d brew=%d ready=%d",
             fam->key, pv, brew_code, ready_code);

    // READY → PREPARING per Nivona Android app expectation. The app
    // re-reads HX ~650 ms post-HE and requires the family-specific
    // brewing code (4 for NIVO 8000, 11 for other Nivona families).
    nivona_fsm_set_process(brew_code, pv);
    nivona_fsm_set_progress(0);
    push_status();

    // Ramp progress 0 → 100 over ~30 s (≈ 60 ticks of 500 ms)
    for (int p = 0; p <= 100; p += 2) {
        if (s_cancel) break;
        nivona_fsm_set_progress((int16_t)p);
        push_status();
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    // Back to family-specific READY (3 for NIVO 8000, 8 for others).
    nivona_fsm_set_process(ready_code, 0);
    nivona_fsm_set_progress(0);
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
    s_active = true;
    s_cancel = false;
    // Pass process_value through task arg. two_cups currently only doubles
    // the ramp length — leave as TODO for Phase 7 polish.
    (void)two_cups;
    xTaskCreate(brew_task, "nivona_brew", 4096,
                (void *)(intptr_t)process_value, 5, &s_task);
    return true;
}

void nivona_brew_cancel(void) {
    if (!s_active) return;
    s_cancel = true;
    ESP_LOGI(TAG, "brew cancel requested");
}

bool nivona_brew_active(void) { return s_active; }
