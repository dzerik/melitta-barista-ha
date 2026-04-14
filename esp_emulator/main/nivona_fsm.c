#include "nivona_fsm.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static nivona_status_t s_status;
static SemaphoreHandle_t s_mutex;

void nivona_fsm_init(void) {
    s_mutex = xSemaphoreCreateMutex();
    // Official Nivona Android app's MakeCoffee() expects process==3 for
    // NIVO 8000 family (4 for other Nivona families, 8 for Melitta).
    // Upstream NIVONA.md describes status code 8 as "ready" for the 79x
    // path. We default to 3 so the app can launch brew immediately.
    // The HA integration treats any process<=10 as OK and uses its own
    // enum semantics, so 3 is compatible with both.
    s_status.process = 3;
    s_status.sub_process = 0;
    s_status.info = 0;
    s_status.manipulation = MANIP_NONE;
    s_status.progress = 0;
    ESP_LOGI("nivona_fsm", "init: process=%d manip=%d",
             s_status.process, s_status.manipulation);
}

static void lock(void)   { xSemaphoreTake(s_mutex, portMAX_DELAY); }
static void unlock(void) { xSemaphoreGive(s_mutex); }

void nivona_fsm_get_status(nivona_status_t *out) {
    lock(); memcpy(out, &s_status, sizeof(s_status)); unlock();
}

void nivona_fsm_set_process(int16_t p, int16_t sp) {
    lock(); s_status.process = p; s_status.sub_process = sp; unlock();
}

void nivona_fsm_set_progress(int16_t p) {
    lock(); s_status.progress = p; unlock();
}

void nivona_fsm_set_manipulation(uint8_t m) {
    lock(); s_status.manipulation = m; unlock();
}

void nivona_fsm_set_info(uint8_t i) {
    lock(); s_status.info = i; unlock();
}

static void put_be16(uint8_t *p, int16_t v) {
    p[0] = (uint8_t)((v >> 8) & 0xFF);
    p[1] = (uint8_t)(v & 0xFF);
}

void nivona_fsm_pack_status(uint8_t out[8]) {
    nivona_status_t s;
    nivona_fsm_get_status(&s);
    put_be16(out + 0, s.process);
    put_be16(out + 2, s.sub_process);
    out[4] = s.info;
    out[5] = s.manipulation;
    put_be16(out + 6, s.progress);
}
