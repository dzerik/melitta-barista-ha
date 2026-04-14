#include "nivona_ble.h"
#include "nivona_gatt.h"

#include <string.h>

#include "esp_log.h"
#include "esp_bt.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "services/gap/ble_svc_gap.h"

static const char *TAG = "nivona_ble";

uint8_t g_own_addr_type;

// Must match the service UUID in nivona_gatt.c (LE byte order)
static const ble_uuid128_t ADV_SVC_UUID = BLE_UUID128_INIT(
    0x1b, 0xc5, 0xd5, 0xa5, 0x02, 0x00, 0x13, 0x98,
    0xe4, 0x11, 0x5c, 0xb3, 0x00, 0xad, 0x00, 0x00);

static int gap_event(struct ble_gap_event *event, void *arg) {
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        ESP_LOGI(TAG, "connect status=%d handle=%d",
                 event->connect.status, event->connect.conn_handle);
        if (event->connect.status == 0) {
            nivona_gatt_on_connect(event->connect.conn_handle);
        } else {
            nivona_ble_start_advertising();
        }
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "disconnect reason=%d", event->disconnect.reason);
        nivona_gatt_on_disconnect();
        nivona_ble_start_advertising();
        break;

    case BLE_GAP_EVENT_ENC_CHANGE:
        ESP_LOGI(TAG, "enc change status=%d", event->enc_change.status);
        break;

    case BLE_GAP_EVENT_SUBSCRIBE:
        nivona_gatt_on_subscribe(event->subscribe.attr_handle,
                                 event->subscribe.cur_notify);
        break;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "mtu updated conn=%d value=%u",
                 event->mtu.conn_handle, event->mtu.value);
        break;

    case BLE_GAP_EVENT_REPEAT_PAIRING: {
        // Peer is bonded but we don't have the keys (e.g. after flash
        // erase). Delete our record so bonding can start fresh.
        struct ble_gap_conn_desc desc;
        if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &desc) == 0) {
            ble_store_util_delete_peer(&desc.peer_id_addr);
            ESP_LOGW(TAG, "repeat pairing: deleted stale bond");
        }
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    }

    case BLE_GAP_EVENT_PASSKEY_ACTION:
        ESP_LOGW(TAG, "passkey action=%d (unexpected for Just Works)",
                 event->passkey.params.action);
        break;

    default:
        break;
    }
    return 0;
}

// Manufacturer-data as captured from a real Eugster machine advertisement:
//   company_id = 0x0319  (Melitta, Eugster OEM)   LE: 19 03
//   payload    = ff ff 00 00 00 01                (6 bytes)
//     - first two bytes = customerId (65535) — required by the Android
//       app's CheckDiscovered filter
// Total: 8 bytes of manufacturer data (incl. 2-byte company_id prefix).
static const uint8_t MFR_DATA[] = {
    0x19, 0x03,                        // company_id = 0x0319 (Melitta)
    0xFF, 0xFF,                        // customerId = 65535 (LE)
    0x00, 0x00, 0x00, 0x00,            // vendor-specific tail (byte-exact to real machine)
};

// 16-bit service UUID list — advertises DIS in primary ADV so the
// Android app can see it during scan (matches real machine).
static const ble_uuid16_t DIS_UUID16 = BLE_UUID16_INIT(0x180A);

const char *nivona_ble_device_name(void) {
    return ble_svc_gap_device_name();
}

void nivona_ble_start_advertising(void) {
    // TX power already set at app_main() boot time (correct ordering per
    // espressif/esp-idf#11001). Re-setting here would be silently ignored.
    const char *name = ble_svc_gap_device_name();

    // Primary adv data: flags + short name + service UUID
    struct ble_hs_adv_fields fields = {0};
    // Primary ADV budget is 31 bytes. Packing:
    //   flags           = 3 bytes
    //   UUID128 (AD00)  = 18 bytes
    //   mfg_data (8+2)  = 10 bytes
    // Total = 31 exact. DIS 16-bit UUID moved to scan response.
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = (ble_uuid128_t *)&ADV_SVC_UUID;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    fields.mfg_data = MFR_DATA;
    fields.mfg_data_len = sizeof(MFR_DATA);

    ESP_LOGI(TAG, "MFR_DATA[%d]: %02x%02x%02x%02x%02x%02x%02x%02x",
             (int)sizeof(MFR_DATA),
             MFR_DATA[0], MFR_DATA[1], MFR_DATA[2], MFR_DATA[3],
             MFR_DATA[4], MFR_DATA[5], MFR_DATA[6], MFR_DATA[7]);

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_set_fields rc=%d (mfg_len=%d, check BLE_HS_ADV_FIELDS_SZ)",
                 rc, fields.mfg_data_len);
        // If 31-byte primary ADV overflows, drop mfg_data for diagnostics
        return;
    }

    // Scan response carries the full device name + DIS 16-bit UUID
    // (32 bytes budget — room for name (24) + UUID16 (4) + tx_pwr (3) = 31)
    struct ble_hs_adv_fields rsp = {0};
    rsp.name = (uint8_t *)name;
    rsp.name_len = strlen(name);
    rsp.name_is_complete = 1;
    rsp.uuids16 = (ble_uuid16_t *)&DIS_UUID16;
    rsp.num_uuids16 = 1;
    rsp.uuids16_is_complete = 1;
    rsp.tx_pwr_lvl_is_present = 1;
    rsp.tx_pwr_lvl = BLE_HS_ADV_TX_PWR_LVL_AUTO;
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_rsp_set_fields rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params adv = {0};
    adv.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv.disc_mode = BLE_GAP_DISC_MODE_GEN;

    rc = ble_gap_adv_start(g_own_addr_type, NULL, BLE_HS_FOREVER,
                           &adv, gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_start rc=%d", rc);
        return;
    }
    ESP_LOGI(TAG, "advertising: %s (mfg=uuid16:29 customerId:65535)", name);
}
