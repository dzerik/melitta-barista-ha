#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_ota_ops.h"
#include "esp_bt.h"

#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"

#include "nivona_ble.h"
#include "nivona_gatt.h"
#include "nivona_fsm.h"
#include "nivona_store.h"
#include "nivona_brew.h"
#include "nivona_cli.h"
#include "nivona_dis.h"
#include "nivona_wifi.h"
#include "nivona_ota.h"
#include "nivona_telnet.h"

static const char *TAG = "nivona_emu";

// Default serial prefix 8107 → family "8000" (NIVO 8xxx). Format required
// by the integration's regex: NIVONA- + 10 digits + 5 dashes.
#define DEFAULT_BLE_NAME "NIVONA-8107000001-----"

void ble_store_config_init(void);

static void on_sync(void) {
    // Nivona/Melitta machines advertise with a random static address
    // starting with F1 (top two bits = 11 → random static per Core spec).
    // HA's ESPHome BLE proxy assumes this type and fails pairing against
    // public addresses. We derive the lower 5 bytes from the controller's
    // built-in MAC so it's stable across reboots (needed for bonding).
    uint8_t base[6];
    ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, base, NULL);  // no-op if not set
    uint8_t rnd_addr[6];
    memcpy(rnd_addr, base, 5);
    rnd_addr[5] = 0xF1;  // random static prefix (top 2 bits = 11)

    int rc = ble_hs_id_set_rnd(rnd_addr);
    if (rc != 0) {
        ESP_LOGE(TAG, "set_rnd rc=%d", rc);
        return;
    }
    g_own_addr_type = BLE_OWN_ADDR_RANDOM;

    ESP_LOGI(TAG, "BLE addr (random static): %02x:%02x:%02x:%02x:%02x:%02x",
             rnd_addr[5], rnd_addr[4], rnd_addr[3],
             rnd_addr[2], rnd_addr[1], rnd_addr[0]);
    nivona_ble_start_advertising();
}

static void on_reset(int reason) {
    ESP_LOGW(TAG, "ble reset reason=%d", reason);
}

static void host_task(void *p) {
    nimble_port_run();
    nimble_port_freertos_deinit();
}

// XIAO ESP32-C6 RF front-end (FM8625H):
//   GPIO3  = RF_SWITCH_EN  → HIGH enables the RF switch (required)
//   GPIO14 = RF_ANT_SELECT → LOW = onboard PCB antenna, HIGH = external U.FL
static void xiao_c6_rf_init(void) {
    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << 3) | (1ULL << 14),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    gpio_set_level(3, 1);   // enable RF switch
    gpio_set_level(14, 1);  // external U.FL antenna
}

void app_main(void) {
    xiao_c6_rf_init();

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    nivona_fsm_init();
    nivona_store_init();
    nivona_brew_init();

    ESP_ERROR_CHECK(nimble_port_init());

    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    // Just Works pairing with bonding
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 0;
    ble_hs_cfg.sm_sc = 1;
    // Minimal key distribution — just encryption key. Some BLE proxies
    // trip on Identity Resolving Key distribution.
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC;

    int rc = nivona_gatt_init();
    if (rc != 0) {
        ESP_LOGE(TAG, "gatt init rc=%d", rc);
        return;
    }
    rc = nivona_dis_init();
    if (rc != 0) {
        ESP_LOGE(TAG, "dis init rc=%d", rc);
        return;
    }

    ESP_ERROR_CHECK(ble_svc_gap_device_name_set(DEFAULT_BLE_NAME));

    // IMPORTANT: TX power must be set AFTER controller enable (done inside
    // nimble_port_init) and AFTER ble_svc_gap_device_name_set but BEFORE
    // ble_store_config_init. NimBLE will silently ignore calls made at the
    // wrong time — espressif/esp-idf#11001.
    // ESP_PWR_LVL_P9 = +9 dBm = max safe for all ESP32 variants.
    esp_err_t tp1 = esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_DEFAULT, ESP_PWR_LVL_P9);
    esp_err_t tp2 = esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV,     ESP_PWR_LVL_P9);
    esp_err_t tp3 = esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_SCAN,    ESP_PWR_LVL_P9);
    ESP_LOGI(TAG, "TX power P9 applied: default=%d adv=%d scan=%d", tp1, tp2, tp3);

    ble_store_config_init();

    nimble_port_freertos_init(host_task);

    // After successful boot, mark current OTA partition as valid so
    // the bootloader doesn't roll back on next reset.
    {
        const esp_partition_t *running = esp_ota_get_running_partition();
        esp_ota_img_states_t state;
        if (running && esp_ota_get_state_partition(running, &state) == ESP_OK) {
            if (state == ESP_OTA_IMG_PENDING_VERIFY) {
                esp_ota_mark_app_valid_cancel_rollback();
                ESP_LOGI(TAG, "OTA partition marked valid");
            }
        }
    }

    nivona_cli_start();

    // WiFi + services (non-blocking). If creds are wrong / WiFi down, BLE
    // still works — WiFi subsystem retries forever.
    nivona_wifi_init();
    nivona_ota_start();
    nivona_telnet_start();

    ESP_LOGI(TAG, "nivona emulator up");
}
