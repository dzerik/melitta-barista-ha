#include "nivona_dis.h"

#include <string.h>

#include "esp_log.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"

static const char *TAG = "nivona_dis";

#define DIS_FIELD_MAX 32

// Values match what a real Nivona machine advertises (NIVONA.md:2303-2310):
// manufacturer="EF", model="EF-BTLE", firmware=numeric, sw="EF_1.00R4__386".
// HA integration / Android app may check these literally.
static char s_manufacturer[DIS_FIELD_MAX] = "EF";
static char s_model[DIS_FIELD_MAX]        = "EF-BTLE";
static char s_serial[DIS_FIELD_MAX]       = "8107000001-----";
static char s_hw_rev[DIS_FIELD_MAX]       = "1";
static char s_fw_rev[DIS_FIELD_MAX]       = "386";
static char s_sw_rev[DIS_FIELD_MAX]       = "EF_1.00R4__386";

static int access_string(uint16_t conn_handle, uint16_t attr_handle,
                         struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) return BLE_ATT_ERR_UNLIKELY;
    const char *s = (const char *)arg;
    return os_mbuf_append(ctxt->om, s, strlen(s)) == 0
           ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static const ble_uuid16_t UUID_DIS         = BLE_UUID16_INIT(0x180A);
static const ble_uuid16_t UUID_MANUFACTURER = BLE_UUID16_INIT(0x2A29);
static const ble_uuid16_t UUID_MODEL       = BLE_UUID16_INIT(0x2A24);
static const ble_uuid16_t UUID_SERIAL      = BLE_UUID16_INIT(0x2A25);
static const ble_uuid16_t UUID_HW_REV      = BLE_UUID16_INIT(0x2A27);
static const ble_uuid16_t UUID_FW_REV      = BLE_UUID16_INIT(0x2A26);
static const ble_uuid16_t UUID_SW_REV      = BLE_UUID16_INIT(0x2A28);

static const struct ble_gatt_chr_def DIS_CHARS[] = {
    { .uuid = &UUID_MANUFACTURER.u, .access_cb = access_string,
      .arg = s_manufacturer, .flags = BLE_GATT_CHR_F_READ },
    { .uuid = &UUID_MODEL.u,        .access_cb = access_string,
      .arg = s_model,        .flags = BLE_GATT_CHR_F_READ },
    { .uuid = &UUID_SERIAL.u,       .access_cb = access_string,
      .arg = s_serial,       .flags = BLE_GATT_CHR_F_READ },
    { .uuid = &UUID_HW_REV.u,       .access_cb = access_string,
      .arg = s_hw_rev,       .flags = BLE_GATT_CHR_F_READ },
    { .uuid = &UUID_FW_REV.u,       .access_cb = access_string,
      .arg = s_fw_rev,       .flags = BLE_GATT_CHR_F_READ },
    { .uuid = &UUID_SW_REV.u,       .access_cb = access_string,
      .arg = s_sw_rev,       .flags = BLE_GATT_CHR_F_READ },
    { 0 }
};

static const struct ble_gatt_svc_def DIS_SVCS[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &UUID_DIS.u,
        .characteristics = DIS_CHARS,
    },
    { 0 }
};

int nivona_dis_init(void) {
    int rc = ble_gatts_count_cfg(DIS_SVCS);
    if (rc != 0) return rc;
    rc = ble_gatts_add_svcs(DIS_SVCS);
    if (rc != 0) return rc;
    ESP_LOGI(TAG, "DIS registered: mfr='%s' model='%s' sn='%s'",
             s_manufacturer, s_model, s_serial);
    return 0;
}

static void copy_field(char *dst, size_t cap, const char *src) {
    if (!src) return;
    size_t n = strlen(src);
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

void nivona_dis_set(const char *mfr, const char *model, const char *sn,
                    const char *hw, const char *fw, const char *sw) {
    copy_field(s_manufacturer, DIS_FIELD_MAX, mfr);
    copy_field(s_model,        DIS_FIELD_MAX, model);
    copy_field(s_serial,       DIS_FIELD_MAX, sn);
    copy_field(s_hw_rev,       DIS_FIELD_MAX, hw);
    copy_field(s_fw_rev,       DIS_FIELD_MAX, fw);
    copy_field(s_sw_rev,       DIS_FIELD_MAX, sw);
}
