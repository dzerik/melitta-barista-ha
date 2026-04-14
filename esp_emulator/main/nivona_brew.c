#include "nivona_brew.h"
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
    ESP_LOGI(TAG, "brew start process=%d", pv);

    // READY(3) → PREPARING(4) per Nivona Android app expectation.
    // The app re-reads HX after 650ms post-HE and requires process==4.
    nivona_fsm_set_process(4, pv);
    nivona_fsm_set_progress(0);
    push_status();

    // Ramp progress 0 → 100 over ~30 s (≈ 60 ticks of 500 ms)
    for (int p = 0; p <= 100; p += 2) {
        if (s_cancel) break;
        nivona_fsm_set_progress((int16_t)p);
        push_status();
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    // Back to READY (3 = Nivona-app convention)
    nivona_fsm_set_process(3, 0);
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
