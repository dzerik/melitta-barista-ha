#include "nivona_cli.h"
#include "nivona_families.h"
#include "nivona_fsm.h"
#include "nivona_store.h"
#include "nivona_brew.h"

#include <string.h>
#include <stdlib.h>

#include "esp_console.h"
#include "esp_log.h"
#include "esp_system.h"
#include "argtable3/argtable3.h"
#include "services/gap/ble_svc_gap.h"
#include "host/ble_store.h"
#include "host/ble_gap.h"
#include "nivona_dis.h"

static const char *TAG = "nivona_cli";

// ---- status ----

static int cmd_status(int argc, char **argv) {
    nivona_status_t s;
    nivona_fsm_get_status(&s);
    printf("process=%d sub=%d info=%u manip=%u progress=%d brew=%d\n",
           s.process, s.sub_process, s.info, s.manipulation, s.progress,
           (int)nivona_brew_active());
    return 0;
}

// ---- diag ----

extern uint32_t g_diag_connects;
extern uint32_t g_diag_disconnects;
extern uint32_t g_diag_subscribes;
extern uint32_t g_diag_ad01_writes;
extern uint32_t g_diag_ad03_writes;
extern uint32_t g_diag_notifies_sent;
extern uint32_t g_diag_notifies_failed;
extern uint32_t g_diag_last_ad03_len;
extern uint8_t  g_diag_last_ad03[64];
extern uint32_t g_diag_hu_rx;
extern uint32_t g_diag_hu_ver_ok;
extern uint32_t g_diag_hu_ver_bad;
extern uint32_t g_diag_hu_resp;
extern uint32_t g_diag_hx_resp;
extern uint32_t g_diag_unhandled;
extern uint32_t g_diag_frame_parsed;

static int cmd_diag(int argc, char **argv) {
    printf("BLE:      connects=%lu disconnects=%lu subscribes=%lu\n",
           (unsigned long)g_diag_connects, (unsigned long)g_diag_disconnects,
           (unsigned long)g_diag_subscribes);
    printf("GATT:     ad01_w=%lu ad03_w=%lu notify_ok=%lu notify_fail=%lu\n",
           (unsigned long)g_diag_ad01_writes, (unsigned long)g_diag_ad03_writes,
           (unsigned long)g_diag_notifies_sent, (unsigned long)g_diag_notifies_failed);
    printf("Parser:   frames_parsed=%lu\n",
           (unsigned long)g_diag_frame_parsed);
    printf("HU:       rx=%lu ver_ok=%lu ver_bad=%lu resp_sent=%lu\n",
           (unsigned long)g_diag_hu_rx, (unsigned long)g_diag_hu_ver_ok,
           (unsigned long)g_diag_hu_ver_bad, (unsigned long)g_diag_hu_resp);
    printf("Commands: hx_resp=%lu unhandled=%lu\n",
           (unsigned long)g_diag_hx_resp, (unsigned long)g_diag_unhandled);
    if (g_diag_last_ad03_len > 0) {
        printf("Last AD03 rx (%lu bytes): ",
               (unsigned long)g_diag_last_ad03_len);
        for (uint32_t i = 0; i < g_diag_last_ad03_len && i < 32; i++)
            printf("%02x", g_diag_last_ad03[i]);
        printf("\n");
    }
    return 0;
}

// ---- trigger <manip> ----

static struct {
    struct arg_str *name;
    struct arg_end *end;
} s_trigger_args;

static int cmd_trigger(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_trigger_args);
    if (nerr) { arg_print_errors(stderr, s_trigger_args.end, "trigger"); return 1; }
    const char *n = s_trigger_args.name->sval[0];
    uint8_t m = MANIP_NONE;
    if      (!strcmp(n, "none"))         m = MANIP_NONE;
    else if (!strcmp(n, "water_empty"))  m = MANIP_WATER_EMPTY;
    else if (!strcmp(n, "beans_empty"))  m = MANIP_BEANS_EMPTY;
    else if (!strcmp(n, "tray_full"))    m = MANIP_TRAY_FULL;
    else if (!strcmp(n, "clean"))        m = MANIP_CLEAN;
    else if (!strcmp(n, "descale"))      m = MANIP_DESCALE;
    else { printf("unknown manip: %s\n", n); return 1; }
    nivona_fsm_set_manipulation(m);
    printf("manipulation = %u\n", m);
    return 0;
}

// ---- brew <process_value> ----

static struct {
    struct arg_int *pv;
    struct arg_end *end;
} s_brew_args;

static int cmd_brew(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_brew_args);
    if (nerr) { arg_print_errors(stderr, s_brew_args.end, "brew"); return 1; }
    int pv = s_brew_args.pv->ival[0];
    if (!nivona_brew_start((int16_t)pv, false)) {
        printf("brew rejected (already active?)\n");
        return 1;
    }
    return 0;
}

static int cmd_cancel(int argc, char **argv) {
    nivona_brew_cancel();
    return 0;
}

// ---- dump ----

static int cmd_dump(int argc, char **argv) {
    nivona_store_dump();
    return 0;
}

// ---- family <key> ----
//
// Switches the advertised BLE name to a serial prefix matching the given
// family key. Change takes effect after reboot (NimBLE caches adv fields).

static struct {
    struct arg_str *key;
    struct arg_end *end;
} s_family_args;

// The canonical family table lives in nivona_families.c. This command
// drives two layers in one shot:
//   1. BLE advertisement + DIS — updates what the app / HA see at the
//      identification layer. NimBLE caches some advertisement fields
//      until re-advertising, so a reboot is still recommended for a
//      clean re-scan.
//   2. FSM process codes — updates the running status block
//      immediately so `status` CLI and the next HX read reflect the
//      new family's READY code (Phase A of the Nivona roadmap).

static int cmd_family(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_family_args);
    if (nerr) { arg_print_errors(stderr, s_family_args.end, "family"); return 1; }
    const char *k = s_family_args.key->sval[0];
    const nivona_family_t *fam = nivona_family_find(k);
    if (fam == NULL) {
        printf("unknown family: %s\n", k);
        // Fall through to the usage print at the bottom of the function.
    } else {
        // 1. Ad/DIS identity
        ble_svc_gap_device_name_set(fam->ble_name);
        nivona_dis_set("EF", "EF-BTLE", fam->ble_name,
                       "1", "386", "EF_1.00R4__386");
        // 2. FSM state — activate new family + reset status to the
        //    family-specific READY code. This lets the very next
        //    HX read reflect the switch without a reboot.
        nivona_family_set(k);
        nivona_fsm_reset_to_ready();
        printf("family=%s name=%s model=%s ready=%d brew=%d\n",
               k, fam->ble_name, fam->model,
               fam->process_ready, fam->process_brewing);
        printf("(status updated in-place; reboot to force a clean re-advertise)\n");
        return 0;
    }
    printf("available: 600, 700, 79x, 900, 900-light, 1030, 1040, 8000\n");
    return 1;
}

// ---- pair ----
//
// Emulate the physical "pairing mode" button on a real Nivona/Melitta.
// Wipes all stored bonds + restarts advertising. HA can then re-bond
// with a fresh slate.

static int cmd_pair(int argc, char **argv) {
    int rc = ble_store_clear();
    printf("cleared bonds: rc=%d\n", rc);
    // stop + restart advertising to force peer-state reset
    ble_gap_adv_stop();
    extern void nivona_ble_start_advertising(void);
    nivona_ble_start_advertising();
    printf("pairing mode: ready for new bond\n");
    return 0;
}

static int cmd_forget(int argc, char **argv) {
    int rc = ble_store_clear();
    printf("wiped bonds: rc=%d — reboot recommended\n", rc);
    return 0;
}

// ---- reboot ----

static int cmd_reboot(int argc, char **argv) {
    printf("rebooting...\n");
    esp_restart();
    return 0;
}

// ---- start ----

void nivona_cli_start(void) {
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t cfg = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    cfg.prompt = "nivona> ";
    cfg.max_cmdline_length = 128;

    esp_console_dev_usb_serial_jtag_config_t dev =
        ESP_CONSOLE_DEV_USB_SERIAL_JTAG_CONFIG_DEFAULT();

    ESP_ERROR_CHECK(esp_console_new_repl_usb_serial_jtag(&dev, &cfg, &repl));

    // status
    const esp_console_cmd_t c_status = {
        .command = "status", .help = "Print FSM status", .func = cmd_status,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_status));

    const esp_console_cmd_t c_diag = {
        .command = "diag", .help = "Print diagnostic counters", .func = cmd_diag,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_diag));

    // trigger <name>
    s_trigger_args.name = arg_str1(NULL, NULL, "<name>",
        "none|water_empty|beans_empty|tray_full|clean|descale");
    s_trigger_args.end = arg_end(2);
    const esp_console_cmd_t c_trigger = {
        .command = "trigger", .help = "Set manipulation state",
        .func = cmd_trigger, .argtable = &s_trigger_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_trigger));

    // brew <pv>
    s_brew_args.pv = arg_int1(NULL, NULL, "<pv>", "process_value (e.g. 1-20)");
    s_brew_args.end = arg_end(2);
    const esp_console_cmd_t c_brew = {
        .command = "brew", .help = "Start brew cycle",
        .func = cmd_brew, .argtable = &s_brew_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_brew));

    const esp_console_cmd_t c_cancel = {
        .command = "cancel", .help = "Cancel active brew", .func = cmd_cancel,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_cancel));

    const esp_console_cmd_t c_dump = {
        .command = "dump", .help = "Dump register store", .func = cmd_dump,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_dump));

    // family <key>
    s_family_args.key = arg_str1(NULL, NULL, "<key>",
        "600|700|79x|900|900-light|1030|1040|8000");
    s_family_args.end = arg_end(2);
    const esp_console_cmd_t c_family = {
        .command = "family", .help = "Switch Nivona family (reboot needed)",
        .func = cmd_family, .argtable = &s_family_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_family));

    const esp_console_cmd_t c_pair = {
        .command = "pair", .help = "Enter pairing mode (wipes stored bonds)",
        .func = cmd_pair,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_pair));

    const esp_console_cmd_t c_forget = {
        .command = "forget", .help = "Wipe stored BLE bonds",
        .func = cmd_forget,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_forget));

    const esp_console_cmd_t c_reboot = {
        .command = "reboot", .help = "Reboot device", .func = cmd_reboot,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_reboot));

    ESP_ERROR_CHECK(esp_console_register_help_command());
    ESP_ERROR_CHECK(esp_console_start_repl(repl));
    ESP_LOGI(TAG, "CLI ready (type 'help')");
}
