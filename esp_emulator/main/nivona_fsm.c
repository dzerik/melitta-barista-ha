#include "nivona_fsm.h"
#include "nivona_families.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static nivona_status_t s_status;
static SemaphoreHandle_t s_mutex;

static void lock(void);
static void unlock(void);

void nivona_fsm_init(void) {
    s_mutex = xSemaphoreCreateMutex();
    // READY process code is family-specific:
    //   NIVO 8000     → 3
    //   All others    → 8
    // (See docs/NIVONA_RE_NOTES.md §Phase A; source:
    //  EugsterMobileApp.MakeCoffee switch on CoffeeMachineModel.)
    // Default family is 8000; CLI `family <key>` + nivona_fsm_reset_to_ready
    // retarget the FSM for other Nivona families.
    const nivona_family_t *fam = nivona_family_current();
    s_status.process = fam->process_ready;
    s_status.sub_process = 0;
    s_status.info = 0;
    s_status.manipulation = MANIP_NONE;
    s_status.progress = 0;
    ESP_LOGI("nivona_fsm", "init: family=%s process=%d manip=%d",
             fam->key, s_status.process, s_status.manipulation);
}

void nivona_fsm_reset_to_ready(void) {
    // Called after a runtime family switch so the advertised status
    // reflects the new family's READY code without requiring reboot.
    const nivona_family_t *fam = nivona_family_current();
    lock();
    s_status.process = fam->process_ready;
    s_status.sub_process = 0;
    s_status.info = 0;
    s_status.manipulation = MANIP_NONE;
    s_status.progress = 0;
    unlock();
    ESP_LOGI("nivona_fsm", "reset_to_ready: family=%s process=%d",
             fam->key, fam->process_ready);
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
    // AUDIT V2 Focus 3/7: the Nivona app parses HX as four BE int16
    // (Process, SubProcess, Message, Progress) — Droid:28601-28623.
    // Bytes 4-5 are a single 16-bit `Message` field, not an
    // info(U8)+manip(U8) pair. The HA integration's
    // MachineStatus.from_payload uses `>hhBBh` (the Melitta parser),
    // so it sees two bytes. We split the 16-bit message into
    //   info = high byte  (always 0 in Nivona-compatible output)
    //   manip = low  byte (the 0/11/20 values the app branches on)
    // When `info != 0` the app computes Message = (info<<8)|manip,
    // which is neither 0, 11, 20 nor within the app's error range
    // (<=6), and the flush/error dialogs silently do not fire.
    // We keep the field writable for CLI debugging, but log a
    // warning whenever a caller tries to set a non-zero info so the
    // Nivona-facing behaviour stays sane.
    if (i != 0) {
        ESP_LOGW("nivona_fsm",
                 "set_info(%u): non-zero info byte is incompatible "
                 "with the Nivona app's BE-int16 Message decode — "
                 "flush/error dialogs will not fire. See audit V2 "
                 "Focus 3/7.", (unsigned)i);
    }
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
    // Bytes 4-5 are one BE int16 from the Nivona app's perspective
    // (Droid:28601-28623). info is the high byte and MUST stay 0
    // for any Message value the app recognises (0, 11, 20);
    // `manipulation` = low byte.
    out[4] = s.info;
    out[5] = s.manipulation;
    put_be16(out + 6, s.progress);
}
