#include "nivona_consumables.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "nivona_consum";

// In-memory state — no persistence across reboot (emulator is a dev
// tool; realistic wear starts fresh every boot). Can be upgraded to
// NVS-backed if needed later.
static uint8_t s_consum[NIVONA_CONSUM_COUNT];
static bool    s_parts[NIVONA_PART_COUNT];
static SemaphoreHandle_t s_mutex = NULL;

static const char *CONSUM_NAMES[NIVONA_CONSUM_COUNT] = {
    [NIVONA_CONSUM_WATER]  = "water",
    [NIVONA_CONSUM_BEANS]  = "beans",
    [NIVONA_CONSUM_TRAY]   = "tray",
    [NIVONA_CONSUM_FILTER] = "filter",
};
static const char *PART_NAMES[NIVONA_PART_COUNT] = {
    [NIVONA_PART_BREW_UNIT]  = "brew_unit",
    [NIVONA_PART_TRAYS]      = "trays",
    [NIVONA_PART_POWDER_LID] = "powder_lid",
};

static void lock(void)   { if (s_mutex) xSemaphoreTake(s_mutex, portMAX_DELAY); }
static void unlock(void) { if (s_mutex) xSemaphoreGive(s_mutex); }

static uint8_t clamp8(int v) {
    if (v < 0) return 0;
    if (v > 100) return 100;
    return (uint8_t)v;
}

void nivona_consumables_init(void) {
    s_mutex = xSemaphoreCreateMutex();
    nivona_consumables_reset();
}

void nivona_consumables_reset(void) {
    lock();
    s_consum[NIVONA_CONSUM_WATER]  = 100;
    s_consum[NIVONA_CONSUM_BEANS]  = 100;
    s_consum[NIVONA_CONSUM_TRAY]   = 0;
    s_consum[NIVONA_CONSUM_FILTER] = 100;
    s_parts[NIVONA_PART_BREW_UNIT]  = true;
    s_parts[NIVONA_PART_TRAYS]      = true;
    s_parts[NIVONA_PART_POWDER_LID] = true;
    unlock();
    ESP_LOGI(TAG, "reset: all tanks full, parts present");
}

uint8_t nivona_consumable_get(nivona_consumable_t c) {
    if (c >= NIVONA_CONSUM_COUNT) return 0;
    lock(); uint8_t v = s_consum[c]; unlock();
    return v;
}

bool nivona_part_get(nivona_part_t p) {
    if (p >= NIVONA_PART_COUNT) return false;
    lock(); bool v = s_parts[p]; unlock();
    return v;
}

void nivona_consumable_set(nivona_consumable_t c, uint8_t pct) {
    if (c >= NIVONA_CONSUM_COUNT) return;
    lock(); s_consum[c] = clamp8(pct); unlock();
    ESP_LOGI(TAG, "set %s=%u", CONSUM_NAMES[c], pct);
}

void nivona_part_set(nivona_part_t p, bool present) {
    if (p >= NIVONA_PART_COUNT) return;
    lock(); s_parts[p] = present; unlock();
    ESP_LOGI(TAG, "set %s=%s", PART_NAMES[p], present ? "present" : "absent");
}

void nivona_consumable_adjust(nivona_consumable_t c, int delta) {
    if (c >= NIVONA_CONSUM_COUNT) return;
    lock();
    int v = (int)s_consum[c] + delta;
    s_consum[c] = clamp8(v);
    unlock();
}

// ---- Consumption hooks ------------------------------------------------
//
// Default consumption profile — 1 espresso ≈ 3% water, ≈ 5% beans, 3% tray.
// With 100% start, that's ~30 espressos before the water runs out and
// ~20 before the bean hopper empties — feels realistic for a "work
// week without a refill" vibe. Tune as we get real-world calibration.

void nivona_consumables_consume_water(uint8_t pct) {
    nivona_consumable_adjust(NIVONA_CONSUM_WATER, -(int)pct);
}

void nivona_consumables_consume_beans(uint8_t pct) {
    nivona_consumable_adjust(NIVONA_CONSUM_BEANS, -(int)pct);
}

void nivona_consumables_fill_tray(uint8_t pct) {
    nivona_consumable_adjust(NIVONA_CONSUM_TRAY, (int)pct);
}

void nivona_consumables_dump(void) {
    lock();
    printf("Consumables:\n");
    for (int i = 0; i < NIVONA_CONSUM_COUNT; i++) {
        printf("  %-10s %3u%%\n", CONSUM_NAMES[i], s_consum[i]);
    }
    printf("Parts:\n");
    for (int i = 0; i < NIVONA_PART_COUNT; i++) {
        printf("  %-12s %s\n", PART_NAMES[i],
               s_parts[i] ? "present" : "absent");
    }
    unlock();
}

const char *nivona_consumable_name(nivona_consumable_t c) {
    if (c >= NIVONA_CONSUM_COUNT) return "?";
    return CONSUM_NAMES[c];
}
const char *nivona_part_name(nivona_part_t p) {
    if (p >= NIVONA_PART_COUNT) return "?";
    return PART_NAMES[p];
}
