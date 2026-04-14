#include "nivona_ota.h"

#include <string.h>

#include "esp_log.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"

static const char *TAG = "nivona_ota";

#define OTA_BUF_SZ 2048

extern uint32_t g_diag_connects, g_diag_disconnects, g_diag_subscribes;
extern uint32_t g_diag_ad01_writes, g_diag_ad03_writes;
extern uint32_t g_diag_notifies_sent, g_diag_notifies_failed;
extern uint32_t g_diag_last_ad03_len;
extern uint8_t  g_diag_last_ad03[64];
extern uint32_t g_diag_hu_rx, g_diag_hu_ver_ok, g_diag_hu_ver_bad, g_diag_hu_resp;
extern uint32_t g_diag_hx_resp, g_diag_unhandled, g_diag_frame_parsed;
extern uint32_t g_diag_try_parse_called, g_diag_cs_mismatch, g_diag_unknown_cmd;
extern uint8_t  g_diag_last_decrypt[32];
extern size_t   g_diag_last_decrypt_len;
extern uint8_t  g_diag_last_recv_cs, g_diag_last_expect_cs;

static esp_err_t status_get(httpd_req_t *req) {
    const esp_app_desc_t *d = esp_app_get_description();
    char body[256];
    int n = snprintf(body, sizeof(body),
        "{\"project\":\"%s\",\"version\":\"%s\",\"idf\":\"%s\",\"compile\":\"%s %s\"}",
        d->project_name, d->version, d->idf_ver, d->date, d->time);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, body, n);
    return ESP_OK;
}

static esp_err_t diag_get(httpd_req_t *req) {
    char body[1024];
    int n = snprintf(body, sizeof(body),
        "{"
        "\"connects\":%lu,\"disconnects\":%lu,\"subscribes\":%lu,"
        "\"ad01_writes\":%lu,\"ad03_writes\":%lu,"
        "\"notify_ok\":%lu,\"notify_fail\":%lu,"
        "\"try_parse\":%lu,\"cs_mismatch\":%lu,\"unknown_cmd\":%lu,"
        "\"frames_parsed\":%lu,"
        "\"hu_rx\":%lu,\"hu_ver_ok\":%lu,\"hu_ver_bad\":%lu,\"hu_resp\":%lu,"
        "\"hx_resp\":%lu,\"unhandled\":%lu,"
        "\"last_ad03_len\":%lu,\"last_ad03\":\"",
        (unsigned long)g_diag_connects, (unsigned long)g_diag_disconnects,
        (unsigned long)g_diag_subscribes, (unsigned long)g_diag_ad01_writes,
        (unsigned long)g_diag_ad03_writes, (unsigned long)g_diag_notifies_sent,
        (unsigned long)g_diag_notifies_failed,
        (unsigned long)g_diag_try_parse_called, (unsigned long)g_diag_cs_mismatch,
        (unsigned long)g_diag_unknown_cmd, (unsigned long)g_diag_frame_parsed,
        (unsigned long)g_diag_hu_rx, (unsigned long)g_diag_hu_ver_ok,
        (unsigned long)g_diag_hu_ver_bad, (unsigned long)g_diag_hu_resp,
        (unsigned long)g_diag_hx_resp, (unsigned long)g_diag_unhandled,
        (unsigned long)g_diag_last_ad03_len);
    for (uint32_t i = 0; i < g_diag_last_ad03_len && i < 32 && n < (int)sizeof(body) - 4; i++)
        n += snprintf(body + n, sizeof(body) - n, "%02x", g_diag_last_ad03[i]);
    n += snprintf(body + n, sizeof(body) - n, "\",\"last_decrypt\":\"");
    for (uint32_t i = 0; i < g_diag_last_decrypt_len && i < 32 && n < (int)sizeof(body) - 4; i++)
        n += snprintf(body + n, sizeof(body) - n, "%02x", g_diag_last_decrypt[i]);
    n += snprintf(body + n, sizeof(body) - n, "\",\"recv_cs\":\"%02x\",\"expect_cs\":\"%02x\"}",
                  g_diag_last_recv_cs, g_diag_last_expect_cs);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, body, n);
    return ESP_OK;
}

static esp_err_t ota_post(httpd_req_t *req) {
    ESP_LOGW(TAG, "OTA begin, content-length=%d", req->content_len);
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no update partition");
        return ESP_FAIL;
    }
    esp_ota_handle_t h;
    esp_err_t err = esp_ota_begin(part, OTA_SIZE_UNKNOWN, &h);
    if (err != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(err));
        return ESP_FAIL;
    }

    char *buf = malloc(OTA_BUF_SZ);
    if (!buf) { esp_ota_abort(h); httpd_resp_send_500(req); return ESP_FAIL; }

    int total = 0;
    int r;
    while ((r = httpd_req_recv(req, buf, OTA_BUF_SZ)) > 0) {
        err = esp_ota_write(h, buf, r);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "ota_write failed: %s", esp_err_to_name(err));
            free(buf); esp_ota_abort(h);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(err));
            return ESP_FAIL;
        }
        total += r;
    }
    free(buf);

    if (r < 0 || total == 0) {
        esp_ota_abort(h);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv error");
        return ESP_FAIL;
    }

    err = esp_ota_end(h);
    if (err != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(err));
        return ESP_FAIL;
    }
    err = esp_ota_set_boot_partition(part);
    if (err != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(err));
        return ESP_FAIL;
    }

    char msg[96];
    int n = snprintf(msg, sizeof(msg), "OK %d bytes, rebooting...\n", total);
    httpd_resp_send(req, msg, n);
    ESP_LOGW(TAG, "OTA done (%d bytes), rebooting", total);
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
    return ESP_OK;
}

static esp_err_t reboot_post(httpd_req_t *req) {
    httpd_resp_send(req, "rebooting\n", 10);
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
    return ESP_OK;
}

int nivona_ota_start(void) {
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 80;
    cfg.max_uri_handlers = 5;
    cfg.recv_wait_timeout = 30;
    cfg.send_wait_timeout = 30;

    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(err));
        return err;
    }

    httpd_uri_t u_status = { "/",       HTTP_GET,  status_get,  NULL };
    httpd_uri_t u_diag   = { "/diag",   HTTP_GET,  diag_get,    NULL };
    httpd_uri_t u_ota    = { "/ota",    HTTP_POST, ota_post,    NULL };
    httpd_uri_t u_reboot = { "/reboot", HTTP_POST, reboot_post, NULL };
    httpd_register_uri_handler(srv, &u_status);
    httpd_register_uri_handler(srv, &u_diag);
    httpd_register_uri_handler(srv, &u_ota);
    httpd_register_uri_handler(srv, &u_reboot);
    ESP_LOGI(TAG, "HTTP OTA server on :80 (GET /, GET /diag, POST /ota, POST /reboot)");
    return 0;
}
