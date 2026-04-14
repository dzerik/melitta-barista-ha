#include "nivona_store.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"

static const char *TAG = "nivona_store";

#define NS_NUM   "niv_num"
#define NS_ALPHA "niv_alpha"

#define NUM_CAP   256
#define ALPHA_CAP 64

typedef struct { int16_t id; int32_t value; bool used; } num_entry_t;
typedef struct { int16_t id; uint8_t data[NIVONA_ALPHA_MAX]; size_t len; bool used; } alpha_entry_t;

static num_entry_t   s_num[NUM_CAP];
static alpha_entry_t s_alpha[ALPHA_CAP];

// ---- NVS helpers -------------------------------------------------------

static void make_key(char *out, size_t out_sz, int16_t id) {
    snprintf(out, out_sz, "%d", (int)id);
}

static void nvs_load_all(void) {
    nvs_handle_t h;
    if (nvs_open(NS_NUM, NVS_READONLY, &h) == ESP_OK) {
        nvs_iterator_t it = NULL;
        esp_err_t err = nvs_entry_find("nvs", NS_NUM, NVS_TYPE_I32, &it);
        while (err == ESP_OK && it) {
            nvs_entry_info_t info;
            nvs_entry_info(it, &info);
            int16_t id = (int16_t)atoi(info.key);
            int32_t v = 0;
            if (nvs_get_i32(h, info.key, &v) == ESP_OK) {
                nivona_store_set_num(id, v);  // populate in-mem, doesn't re-write
            }
            err = nvs_entry_next(&it);
        }
        nvs_release_iterator(it);
        nvs_close(h);
    }
    if (nvs_open(NS_ALPHA, NVS_READONLY, &h) == ESP_OK) {
        nvs_iterator_t it = NULL;
        esp_err_t err = nvs_entry_find("nvs", NS_ALPHA, NVS_TYPE_BLOB, &it);
        while (err == ESP_OK && it) {
            nvs_entry_info_t info;
            nvs_entry_info(it, &info);
            int16_t id = (int16_t)atoi(info.key);
            uint8_t buf[NIVONA_ALPHA_MAX];
            size_t sz = sizeof(buf);
            if (nvs_get_blob(h, info.key, buf, &sz) == ESP_OK) {
                nivona_store_set_alpha(id, buf, sz);
            }
            err = nvs_entry_next(&it);
        }
        nvs_release_iterator(it);
        nvs_close(h);
    }
}

// ---- Init with sensible defaults --------------------------------------

static void seed_defaults(void) {
    // Cup counters etc. left at zero. Profile names as fallback.
    const char *names[] = { "Profile 1", "Profile 2", "Profile 3", "Profile 4" };
    // IDs are family-specific; we seed the most common Nivona slots.
    // If integration reads other IDs, they'll get empty strings which is fine.
    for (int i = 0; i < 4; i++) {
        int16_t id = (int16_t)(0x0100 + i);  // placeholder range
        nivona_store_set_alpha(id, (const uint8_t *)names[i], strlen(names[i]));
    }
}

void nivona_store_init(void) {
    memset(s_num, 0, sizeof(s_num));
    memset(s_alpha, 0, sizeof(s_alpha));
    nvs_load_all();
    seed_defaults();
    ESP_LOGI(TAG, "store initialised");
}

// ---- Numerical --------------------------------------------------------

int32_t nivona_store_get_num(int16_t id) {
    for (int i = 0; i < NUM_CAP; i++) {
        if (s_num[i].used && s_num[i].id == id) return s_num[i].value;
    }
    return 0;
}

void nivona_store_set_num(int16_t id, int32_t value) {
    int free_slot = -1;
    for (int i = 0; i < NUM_CAP; i++) {
        if (s_num[i].used && s_num[i].id == id) {
            s_num[i].value = value;
            goto persist;
        }
        if (!s_num[i].used && free_slot < 0) free_slot = i;
    }
    if (free_slot < 0) {
        ESP_LOGW(TAG, "num table full, dropping id=%d", id);
        return;
    }
    s_num[free_slot].id = id;
    s_num[free_slot].value = value;
    s_num[free_slot].used = true;
persist:
    {
        nvs_handle_t h;
        if (nvs_open(NS_NUM, NVS_READWRITE, &h) == ESP_OK) {
            char key[8]; make_key(key, sizeof(key), id);
            nvs_set_i32(h, key, value);
            nvs_commit(h);
            nvs_close(h);
        }
    }
}

// ---- Alpha ------------------------------------------------------------

size_t nivona_store_get_alpha(int16_t id, uint8_t *out, size_t max) {
    for (int i = 0; i < ALPHA_CAP; i++) {
        if (s_alpha[i].used && s_alpha[i].id == id) {
            size_t n = s_alpha[i].len < max ? s_alpha[i].len : max;
            memcpy(out, s_alpha[i].data, n);
            return n;
        }
    }
    return 0;
}

void nivona_store_set_alpha(int16_t id, const uint8_t *data, size_t len) {
    if (len > NIVONA_ALPHA_MAX) len = NIVONA_ALPHA_MAX;
    int free_slot = -1;
    for (int i = 0; i < ALPHA_CAP; i++) {
        if (s_alpha[i].used && s_alpha[i].id == id) {
            memcpy(s_alpha[i].data, data, len);
            s_alpha[i].len = len;
            goto persist;
        }
        if (!s_alpha[i].used && free_slot < 0) free_slot = i;
    }
    if (free_slot < 0) {
        ESP_LOGW(TAG, "alpha table full, dropping id=%d", id);
        return;
    }
    s_alpha[free_slot].id = id;
    memcpy(s_alpha[free_slot].data, data, len);
    s_alpha[free_slot].len = len;
    s_alpha[free_slot].used = true;
persist:
    {
        nvs_handle_t h;
        if (nvs_open(NS_ALPHA, NVS_READWRITE, &h) == ESP_OK) {
            char key[8]; make_key(key, sizeof(key), id);
            nvs_set_blob(h, key, data, len);
            nvs_commit(h);
            nvs_close(h);
        }
    }
}

void nivona_store_dump(void) {
    ESP_LOGI(TAG, "-- numerical --");
    for (int i = 0; i < NUM_CAP; i++) {
        if (s_num[i].used) ESP_LOGI(TAG, "  %d = %ld", s_num[i].id, (long)s_num[i].value);
    }
    ESP_LOGI(TAG, "-- alpha --");
    for (int i = 0; i < ALPHA_CAP; i++) {
        if (s_alpha[i].used) {
            char tmp[NIVONA_ALPHA_MAX + 1];
            size_t n = s_alpha[i].len < NIVONA_ALPHA_MAX ? s_alpha[i].len : NIVONA_ALPHA_MAX;
            memcpy(tmp, s_alpha[i].data, n); tmp[n] = 0;
            ESP_LOGI(TAG, "  %d = \"%s\"", s_alpha[i].id, tmp);
        }
    }
}
