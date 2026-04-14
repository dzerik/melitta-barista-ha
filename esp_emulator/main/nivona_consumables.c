#include "nivona_consumables.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

static const char *TAG = "nivona_consum";

// NVS-backed state (emu-v0.5.0+). On boot consumables resume where
// they left off — a real machine keeps its water level and cup
// counters across power cycles. `factory_reset` CLI wipes the
// namespace to restart fresh.
#define NS_CONSUM "niv_consum"
#define KEY_TANKS "tanks"   // blob, NIVONA_CONSUM_COUNT bytes
#define KEY_PARTS "parts"   // blob, NIVONA_PART_COUNT bytes

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

// Snapshot the whole state to NVS. Called on every change — NVS is
// wear-levelled and tank updates happen at most a few times per minute,
// so this is well within safety margins.
static void persist(void) {
    nvs_handle_t h;
    if (nvs_open(NS_CONSUM, NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_blob(h, KEY_TANKS, s_consum, sizeof(s_consum));
    // Pack bool[] → uint8[] for blob write.
    uint8_t parts[NIVONA_PART_COUNT];
    for (int i = 0; i < NIVONA_PART_COUNT; i++) parts[i] = s_parts[i] ? 1 : 0;
    nvs_set_blob(h, KEY_PARTS, parts, sizeof(parts));
    nvs_commit(h);
    nvs_close(h);
}

// Load. Returns true if any state was restored; false → caller
// should fall back to factory defaults.
static bool restore(void) {
    nvs_handle_t h;
    if (nvs_open(NS_CONSUM, NVS_READONLY, &h) != ESP_OK) return false;
    size_t sz = sizeof(s_consum);
    bool ok = (nvs_get_blob(h, KEY_TANKS, s_consum, &sz) == ESP_OK &&
               sz == sizeof(s_consum));
    if (ok) {
        uint8_t parts[NIVONA_PART_COUNT];
        sz = sizeof(parts);
        if (nvs_get_blob(h, KEY_PARTS, parts, &sz) == ESP_OK &&
            sz == sizeof(parts)) {
            for (int i = 0; i < NIVONA_PART_COUNT; i++) {
                s_parts[i] = parts[i] != 0;
            }
        } else {
            for (int i = 0; i < NIVONA_PART_COUNT; i++) s_parts[i] = true;
        }
    }
    nvs_close(h);
    return ok;
}

void nivona_consumables_init(void) {
    s_mutex = xSemaphoreCreateMutex();
    if (!restore()) {
        ESP_LOGI(TAG, "no persisted state → factory defaults");
        nivona_consumables_reset();
    } else {
        ESP_LOGI(TAG, "restored: water=%u beans=%u tray=%u filter=%u",
                 s_consum[NIVONA_CONSUM_WATER],
                 s_consum[NIVONA_CONSUM_BEANS],
                 s_consum[NIVONA_CONSUM_TRAY],
                 s_consum[NIVONA_CONSUM_FILTER]);
    }
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
    persist();
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
    persist();
    ESP_LOGI(TAG, "set %s=%u", CONSUM_NAMES[c], pct);
}

void nivona_part_set(nivona_part_t p, bool present) {
    if (p >= NIVONA_PART_COUNT) return;
    lock(); s_parts[p] = present; unlock();
    persist();
    ESP_LOGI(TAG, "set %s=%s", PART_NAMES[p], present ? "present" : "absent");
}

void nivona_consumable_adjust(nivona_consumable_t c, int delta) {
    if (c >= NIVONA_CONSUM_COUNT) return;
    lock();
    int v = (int)s_consum[c] + delta;
    s_consum[c] = clamp8(v);
    unlock();
    persist();
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
