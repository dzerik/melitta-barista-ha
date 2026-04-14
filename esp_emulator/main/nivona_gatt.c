#include "nivona_gatt.h"
#include "nivona_frame.h"

#include <string.h>

#include "esp_log.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

static const char *TAG = "nivona_gatt";

// 128-bit UUIDs — Nivona custom service AD00 and its characteristics.
// All are listed here even though AD04/AD05 are stubbed, because the
// Android app's CoffeeMachine::Initialize() binds AD01 / AD03 / AD02 /
// AD06 (required) and optionally AD04 / AD05 (NIVONA.md:331-345).
#define NIVONA_UUID_128(b12, b13)                              \
    BLE_UUID128_INIT(                                          \
        0x1b, 0xc5, 0xd5, 0xa5, 0x02, 0x00, 0x13, 0x98,        \
        0xe4, 0x11, 0x5c, 0xb3, (b12), (b13), 0x00, 0x00)

static const ble_uuid128_t SVC_UUID          = NIVONA_UUID_128(0x00, 0xad);
static const ble_uuid128_t CHAR_AD01_UUID    = NIVONA_UUID_128(0x01, 0xad);
static const ble_uuid128_t CHAR_AD02_UUID    = NIVONA_UUID_128(0x02, 0xad);
static const ble_uuid128_t CHAR_AD03_UUID    = NIVONA_UUID_128(0x03, 0xad);
static const ble_uuid128_t CHAR_AD04_UUID    = NIVONA_UUID_128(0x04, 0xad);
static const ble_uuid128_t CHAR_AD05_UUID    = NIVONA_UUID_128(0x05, 0xad);
static const ble_uuid128_t CHAR_AD06_UUID    = NIVONA_UUID_128(0x06, 0xad);

static uint16_t s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t s_notify_handle = 0;
static bool     s_notify_subscribed = false;

// Diagnostic counters — visible via CLI `diag`
uint32_t g_diag_connects = 0;
uint32_t g_diag_disconnects = 0;
uint32_t g_diag_subscribes = 0;
uint32_t g_diag_ad01_writes = 0;
uint32_t g_diag_ad03_writes = 0;
uint32_t g_diag_notifies_sent = 0;
uint32_t g_diag_notifies_failed = 0;
uint32_t g_diag_last_ad03_len = 0;
uint8_t  g_diag_last_ad03[64] = {0};

// AD06 = device-name read/write. Stored here (not in BLE stack so we
// don't have to re-advertise).
static char s_ad06_name[32] = "";

// ---- Access callbacks --------------------------------------------------

static int on_ad01_write(uint16_t conn, uint16_t ah,
                         struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_UNLIKELY;
    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    uint8_t buf[128];
    if (len > sizeof(buf)) len = sizeof(buf);
    ble_hs_mbuf_to_flat(ctxt->om, buf, len, NULL);
    g_diag_ad01_writes++;
    ESP_LOGI(TAG, "AD01 write %u bytes", len);
    // Real machine uses AD01 for session control. App Initialize() does
    // NOT write AD01 (NIVONA.md:356). We log and accept anything.
    return 0;
}

static int on_ad03_write(uint16_t conn, uint16_t ah,
                         struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_UNLIKELY;
    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    uint8_t buf[256];
    if (len > sizeof(buf)) len = sizeof(buf);
    ble_hs_mbuf_to_flat(ctxt->om, buf, len, NULL);
    g_diag_ad03_writes++;
    g_diag_last_ad03_len = len;
    memcpy(g_diag_last_ad03, buf, len < sizeof(g_diag_last_ad03) ? len : sizeof(g_diag_last_ad03));
    ESP_LOGI(TAG, "AD03 <- rx %u bytes", len);
    nivona_frame_feed(buf, len);
    return 0;
}

static int on_ad06_access(uint16_t conn, uint16_t ah,
                          struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        const char *name = s_ad06_name[0] ? s_ad06_name : ble_svc_gap_device_name();
        return os_mbuf_append(ctxt->om, name, strlen(name)) == 0
               ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
        if (len >= sizeof(s_ad06_name)) len = sizeof(s_ad06_name) - 1;
        ble_hs_mbuf_to_flat(ctxt->om, s_ad06_name, len, NULL);
        s_ad06_name[len] = 0;
        ESP_LOGI(TAG, "AD06 device-name write: '%s'", s_ad06_name);
        return 0;
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static int on_ad0x_stub(uint16_t conn, uint16_t ah,
                        struct ble_gatt_access_ctxt *ctxt, void *arg) {
    // AD04 / AD05 — discovered but not used in traced logic
    // (NIVONA.md:110-113). Accept any op, return empty.
    return 0;
}

// ---- Service definition ------------------------------------------------

// NOTE: NimBLE requires notify chars to come LAST or at least have their
// val_handle set. We keep AD02 first as the primary notify path, then
// write/read chars.
static const struct ble_gatt_chr_def CHARS[] = {
    {
        .uuid = &CHAR_AD02_UUID.u,
        .access_cb = on_ad0x_stub,
        .flags = BLE_GATT_CHR_F_NOTIFY | BLE_GATT_CHR_F_READ,
        .val_handle = &s_notify_handle,
    },
    {
        .uuid = &CHAR_AD01_UUID.u,
        .access_cb = on_ad01_write,
        .flags = BLE_GATT_CHR_F_WRITE,
    },
    {
        .uuid = &CHAR_AD03_UUID.u,
        .access_cb = on_ad03_write,
        // Allow BOTH write types — some proxies prefer write-no-response
        .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
    },
    {
        .uuid = &CHAR_AD04_UUID.u,
        .access_cb = on_ad0x_stub,
        .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE,
    },
    {
        .uuid = &CHAR_AD05_UUID.u,
        .access_cb = on_ad0x_stub,
        .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE,
    },
    {
        .uuid = &CHAR_AD06_UUID.u,
        .access_cb = on_ad06_access,
        .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE,
    },
    { 0 }
};

static const struct ble_gatt_svc_def SVCS[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &SVC_UUID.u,
        .characteristics = CHARS,
    },
    { 0 }
};

int nivona_gatt_init(void) {
    ble_svc_gap_init();
    ble_svc_gatt_init();
    int rc = ble_gatts_count_cfg(SVCS);
    if (rc != 0) {
        ESP_LOGE(TAG, "count_cfg failed: rc=%d", rc);
        return rc;
    }
    rc = ble_gatts_add_svcs(SVCS);
    if (rc != 0) {
        ESP_LOGE(TAG, "add_svcs failed: rc=%d", rc);
    }
    return rc;
}

void nivona_gatt_on_connect(uint16_t conn_handle) {
    g_diag_connects++;
    s_conn_handle = conn_handle;
    s_notify_subscribed = false;
}

void nivona_gatt_on_disconnect(void) {
    g_diag_disconnects++;
    s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
    s_notify_subscribed = false;
    nivona_frame_reset();
}

void nivona_gatt_on_subscribe(uint16_t attr_handle, int cur_notify) {
    g_diag_subscribes++;
    ESP_LOGI(TAG, "SUBSCRIBE attr_handle=%u (notify_h=%u) cur_notify=%d",
             attr_handle, s_notify_handle, cur_notify);
    if (attr_handle == s_notify_handle) {
        s_notify_subscribed = cur_notify != 0;
        ESP_LOGI(TAG, "AD02 notify subscribed=%d", s_notify_subscribed);
    }
}

void nivona_gatt_notify(const uint8_t *data, size_t len) {
    if (!s_notify_subscribed || s_conn_handle == BLE_HS_CONN_HANDLE_NONE) return;
    ESP_LOGI(TAG, "AD02 -> tx %u bytes", (unsigned)len);
    struct os_mbuf *om = ble_hs_mbuf_from_flat(data, len);
    if (!om) return;
    int rc = ble_gatts_notify_custom(s_conn_handle, s_notify_handle, om);
    if (rc != 0) { g_diag_notifies_failed++; ESP_LOGW(TAG, "notify rc=%d", rc); }
    else { g_diag_notifies_sent++; }
}
