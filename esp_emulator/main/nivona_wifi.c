#include "nivona_wifi.h"

#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#else
#error "Create main/wifi_secrets.h (copy from main/wifi_secrets.h.template) "\
       "with your WIFI_SSID / WIFI_PASS / MDNS_HOSTNAME before building."
#endif

#include <string.h>

#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "mdns.h"
#include "nvs_flash.h"

static const char *TAG = "nivona_wifi";
static volatile bool s_connected = false;

static void event_handler(void *arg, esp_event_base_t base,
                          int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        ESP_LOGW(TAG, "disconnected, reconnecting...");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "got IP: " IPSTR, IP2STR(&ev->ip_info.ip));
        s_connected = true;
    }
}

int nivona_wifi_init(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               event_handler, NULL));

    wifi_config_t wc = { 0 };
    strncpy((char *)wc.sta.ssid,     WIFI_SSID, sizeof(wc.sta.ssid));
    strncpy((char *)wc.sta.password, WIFI_PASS, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_MIN_MODEM));
    ESP_ERROR_CHECK(esp_wifi_start());

    // mDNS so host can find us by name
    ESP_ERROR_CHECK(mdns_init());
    ESP_ERROR_CHECK(mdns_hostname_set(MDNS_HOSTNAME));
    mdns_service_add(NULL, "_http",   "_tcp", 80, NULL, 0);
    mdns_service_add(NULL, "_telnet", "_tcp", 23, NULL, 0);

    ESP_LOGI(TAG, "STA init, connecting to '%s', hostname=%s.local",
             WIFI_SSID, MDNS_HOSTNAME);
    return 0;
}

bool nivona_wifi_connected(void) { return s_connected; }
