#include "nivona_telnet.h"

#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>

#include "esp_log.h"
#include "esp_console.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

static const char *TAG = "nivona_telnet";

#define TELNET_PORT 23

static int s_client_fd = -1;
static SemaphoreHandle_t s_mutex;
static vprintf_like_t s_orig_vprintf = NULL;

// ---- Log redirect: mirror stdout to any connected telnet client -------

static int telnet_vprintf(const char *fmt, va_list args) {
    va_list copy;
    va_copy(copy, args);
    int r = s_orig_vprintf ? s_orig_vprintf(fmt, args) : vprintf(fmt, args);
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    int fd = s_client_fd;
    xSemaphoreGive(s_mutex);
    if (fd >= 0) {
        char buf[256];
        int n = vsnprintf(buf, sizeof(buf), fmt, copy);
        if (n > 0) {
            if (n > (int)sizeof(buf)) n = sizeof(buf);
            (void)send(fd, buf, n, MSG_DONTWAIT);
        }
    }
    va_end(copy);
    return r;
}

// ---- Command execution using existing esp_console registry -----------

static void exec_line(int fd, char *line) {
    // Strip CR/LF
    size_t n = strlen(line);
    while (n && (line[n-1] == '\r' || line[n-1] == '\n' || line[n-1] == ' ')) {
        line[--n] = 0;
    }
    if (!n) { send(fd, "nivona> ", 8, 0); return; }

    // Redirect stdout to a memory buffer so command's printf() output
    // is captured and sent back to the telnet client.
    char capture[1024] = {0};
    FILE *mem = fmemopen(capture, sizeof(capture) - 1, "w");
    FILE *saved_stdout = stdout;
    if (mem) stdout = mem;

    int ret = 0;
    esp_err_t err = esp_console_run(line, &ret);

    if (mem) {
        fflush(mem);
        stdout = saved_stdout;
        fclose(mem);
    }

    // Send captured command output
    size_t clen = strlen(capture);
    if (clen) send(fd, capture, clen, 0);

    // Append status line for errors
    char buf[96];
    const char *extra = NULL;
    if (err == ESP_ERR_NOT_FOUND) extra = "command not found\n";
    else if (err == ESP_ERR_INVALID_ARG) extra = "invalid args\n";
    else if (err == ESP_OK && ret != 0) {
        snprintf(buf, sizeof(buf), "(rc=%d)\n", ret);
        extra = buf;
    }
    if (extra) send(fd, extra, strlen(extra), 0);
    send(fd, "nivona> ", 8, 0);
}

// ---- Telnet server task -----------------------------------------------

static void client_task(void *arg) {
    int fd = (int)(intptr_t)arg;
    const char *banner = "Nivona emulator telnet. Type 'help'.\nnivona> ";
    send(fd, banner, strlen(banner), 0);

    char buf[128];
    size_t pos = 0;
    while (1) {
        char c;
        int r = recv(fd, &c, 1, 0);
        if (r <= 0) break;
        // Skip telnet IAC sequences (0xFF followed by 2 bytes)
        if ((uint8_t)c == 0xFF) {
            recv(fd, &c, 1, 0); recv(fd, &c, 1, 0);
            continue;
        }
        if (c == '\n' || c == '\r') {
            buf[pos] = 0;
            exec_line(fd, buf);
            pos = 0;
        } else if (pos + 1 < sizeof(buf)) {
            buf[pos++] = c;
        }
    }

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if (s_client_fd == fd) s_client_fd = -1;
    xSemaphoreGive(s_mutex);
    close(fd);
    vTaskDelete(NULL);
}

static void listen_task(void *arg) {
    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    int yes = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr = {
        .sin_family = AF_INET, .sin_port = htons(TELNET_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "bind :23 failed errno=%d", errno);
        close(lfd); vTaskDelete(NULL); return;
    }
    if (listen(lfd, 1) < 0) {
        ESP_LOGE(TAG, "listen failed errno=%d", errno);
        close(lfd); vTaskDelete(NULL); return;
    }
    ESP_LOGI(TAG, "telnet listening on :%d", TELNET_PORT);

    while (1) {
        int cfd = accept(lfd, NULL, NULL);
        if (cfd < 0) { vTaskDelay(pdMS_TO_TICKS(100)); continue; }
        xSemaphoreTake(s_mutex, portMAX_DELAY);
        if (s_client_fd >= 0) {
            // Evict previous client
            close(s_client_fd);
        }
        s_client_fd = cfd;
        xSemaphoreGive(s_mutex);
        xTaskCreate(client_task, "telnet_cli", 4096,
                    (void *)(intptr_t)cfd, 5, NULL);
    }
}

int nivona_telnet_start(void) {
    s_mutex = xSemaphoreCreateMutex();
    s_orig_vprintf = esp_log_set_vprintf(telnet_vprintf);
    xTaskCreate(listen_task, "telnet_lst", 4096, NULL, 5, NULL);
    return 0;
}
