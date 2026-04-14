#include "nivona_cli.h"
#include "nivona_consumables.h"
#include "nivona_families.h"
#include "nivona_fsm.h"
#include "nivona_maint.h"
#include "nivona_maint_cycle.h"
#include "nivona_store.h"
#include "nivona_brew.h"

#include <string.h>
#include <stdlib.h>

#include "esp_console.h"
#include "esp_log.h"
#include "esp_system.h"
#include "argtable3/argtable3.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
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
    // Legacy `trigger` accepts both the old emulator names and the
    // canonical Manipulation enum labels from const.py. Prefer the
    // latter — the old aliases are kept so historical scripts still
    // work after the Phase D enum rename.
    uint8_t m = MANIP_NONE;
    if      (!strcmp(n, "none"))           m = MANIP_NONE;
    else if (!strcmp(n, "bu_removed"))     m = MANIP_BU_REMOVED;
    else if (!strcmp(n, "trays_missing"))  m = MANIP_TRAYS_MISSING;
    else if (!strcmp(n, "empty_trays"))    m = MANIP_EMPTY_TRAYS;
    else if (!strcmp(n, "fill_water"))     m = MANIP_FILL_WATER;
    else if (!strcmp(n, "close_powder"))   m = MANIP_CLOSE_POWDER_LID;
    else if (!strcmp(n, "fill_powder"))    m = MANIP_FILL_POWDER;
    else if (!strcmp(n, "move_cup"))       m = MANIP_MOVE_CUP;
    else if (!strcmp(n, "flush"))          m = MANIP_FLUSH_REQUIRED;
    // Legacy aliases (pre-Phase-D):
    else if (!strcmp(n, "water_empty"))    m = MANIP_FILL_WATER;
    else if (!strcmp(n, "tray_full"))      m = MANIP_EMPTY_TRAYS;
    else if (!strcmp(n, "beans_empty"))    m = MANIP_NONE; // no code in canonical enum
    else if (!strcmp(n, "clean"))          m = MANIP_FLUSH_REQUIRED;
    else if (!strcmp(n, "descale"))        m = MANIP_FLUSH_REQUIRED;
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

// ---- tanks / parts (Phase D) ------------------------------------------

static struct {
    struct arg_str *name;
    struct arg_int *pct;
    struct arg_end *end;
} s_tank_args;

static nivona_consumable_t consum_by_name(const char *name) {
    if (!strcmp(name, "water"))  return NIVONA_CONSUM_WATER;
    if (!strcmp(name, "beans"))  return NIVONA_CONSUM_BEANS;
    if (!strcmp(name, "tray"))   return NIVONA_CONSUM_TRAY;
    if (!strcmp(name, "filter")) return NIVONA_CONSUM_FILTER;
    return NIVONA_CONSUM_COUNT; // sentinel "not found"
}

static nivona_part_t part_by_name(const char *name) {
    if (!strcmp(name, "brew_unit"))  return NIVONA_PART_BREW_UNIT;
    if (!strcmp(name, "trays"))      return NIVONA_PART_TRAYS;
    if (!strcmp(name, "powder_lid")) return NIVONA_PART_POWDER_LID;
    return NIVONA_PART_COUNT;
}

static int cmd_tank(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_tank_args);
    if (nerr) { arg_print_errors(stderr, s_tank_args.end, "tank"); return 1; }
    const char *name = s_tank_args.name->sval[0];
    nivona_consumable_t c = consum_by_name(name);
    if (c == NIVONA_CONSUM_COUNT) {
        printf("unknown tank: %s  (water|beans|tray|filter)\n", name);
        return 1;
    }
    int pct = s_tank_args.pct->ival[0];
    nivona_consumable_set(c, (uint8_t)(pct < 0 ? 0 : (pct > 100 ? 100 : pct)));
    nivona_maint_reevaluate();
    printf("%s=%u%% (manipulation re-evaluated)\n", name, nivona_consumable_get(c));
    return 0;
}

static struct {
    struct arg_str *name;
    struct arg_end *end;
} s_fix_args;

static int cmd_fix(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_fix_args);
    if (nerr) { arg_print_errors(stderr, s_fix_args.end, "fix"); return 1; }
    const char *name = s_fix_args.name->sval[0];

    // Shortcuts for the most common "oops, refill" flow:
    if (!strcmp(name, "water"))  { nivona_consumable_set(NIVONA_CONSUM_WATER, 100); goto done; }
    if (!strcmp(name, "beans"))  { nivona_consumable_set(NIVONA_CONSUM_BEANS, 100); goto done; }
    if (!strcmp(name, "tray"))   { nivona_consumable_set(NIVONA_CONSUM_TRAY, 0);    goto done; }
    if (!strcmp(name, "filter")) { nivona_consumable_set(NIVONA_CONSUM_FILTER, 100); goto done; }
    if (!strcmp(name, "all"))    { nivona_consumables_reset();                      goto done; }

    // Parts — assume user means "re-seat" / install:
    nivona_part_t p = part_by_name(name);
    if (p != NIVONA_PART_COUNT) { nivona_part_set(p, true); goto done; }

    printf("unknown target: %s  (water|beans|tray|filter|all|brew_unit|trays|powder_lid)\n", name);
    return 1;
done:
    nivona_maint_reevaluate();
    printf("fixed %s; maintenance re-evaluated\n", name);
    return 0;
}

static struct {
    struct arg_str *name;
    struct arg_str *state; // "on" / "off"
    struct arg_end *end;
} s_part_args;

static int cmd_part(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_part_args);
    if (nerr) { arg_print_errors(stderr, s_part_args.end, "part"); return 1; }
    const char *name = s_part_args.name->sval[0];
    const char *state = s_part_args.state->sval[0];
    nivona_part_t p = part_by_name(name);
    if (p == NIVONA_PART_COUNT) {
        printf("unknown part: %s  (brew_unit|trays|powder_lid)\n", name);
        return 1;
    }
    bool present = (!strcmp(state, "on") || !strcmp(state, "present") ||
                    !strcmp(state, "in")  || !strcmp(state, "1"));
    nivona_part_set(p, present);
    nivona_maint_reevaluate();
    printf("%s=%s (manipulation re-evaluated)\n",
           name, present ? "present" : "absent");
    return 0;
}

static int cmd_tanks_dump(int argc, char **argv) {
    (void)argc; (void)argv;
    nivona_consumables_dump();
    return 0;
}

// ---- factory_reset ----------------------------------------------------
//
// Wipe all emulator-owned NVS namespaces and reboot. Does NOT touch
// `nvs.net80211` (WiFi creds) or NimBLE's own namespaces — those
// belong to IDF components and clearing them would brick OTA / auto-
// reconnect. If the user really wants a full factory wipe there's
// already `forget` (BLE bonds) + the stock `nvs_erase` esptool flow.

// ---- maint <cycle> (Phase E) ------------------------------------------

static struct {
    struct arg_str *name;
    struct arg_end *end;
} s_maint_args;

static int cmd_maint(int argc, char **argv) {
    int nerr = arg_parse(argc, argv, (void **)&s_maint_args);
    if (nerr) { arg_print_errors(stderr, s_maint_args.end, "maint"); return 1; }
    const char *name = s_maint_args.name->sval[0];
    int kind = nivona_maint_cycle_from_name(name);
    if (kind < 0) {
        printf("unknown cycle: %s\n", name);
        printf("available: descale, easy_clean, intensive_clean, "
               "filter_insert, filter_replace, filter_remove, "
               "evaporating, rinse\n");
        return 1;
    }
    if (!nivona_maint_cycle_start((nivona_cycle_kind_t)kind)) {
        printf("cycle rejected (already active or brew in progress)\n");
        return 1;
    }
    printf("started %s — HX progress notifications streaming\n", name);
    return 0;
}

// ---- stats (Phase B-lite) --------------------------------------------

static int cmd_stats(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("Stat counters (HR id -> value):\n");
    static const struct { int16_t id; const char *label; } STAT_TABLE[] = {
        {200, "espresso"}, {201, "coffee/cream"}, {202, "lungo/americano"},
        {203, "americano/cappuccino"}, {204, "cappuccino/latte"},
        {205, "latte_macchiato"}, {206, "milk"}, {207, "hot_water"},
        {208, "my_coffee"}, {213, "total_beverages"},
        {214, "clean_coffee_system"}, {215, "clean_frother"},
        {216, "rinse_cycles"}, {219, "filter_changes"},
        {220, "descaling"}, {221, "beverages_via_app"},
        {600, "descale_%"}, {601, "descale_warn"},
        {610, "brew_unit_%"}, {611, "brew_unit_warn"},
        {620, "frother_%"}, {621, "frother_warn"},
        {640, "filter_%"}, {641, "filter_warn"},
    };
    for (size_t i = 0; i < sizeof(STAT_TABLE)/sizeof(STAT_TABLE[0]); i++) {
        int32_t v = nivona_store_get_num(STAT_TABLE[i].id);
        if (v != 0 || STAT_TABLE[i].id >= 600) {  // always show gauges
            printf("  %3d  %-22s %d\n",
                   STAT_TABLE[i].id, STAT_TABLE[i].label, (int)v);
        }
    }
    return 0;
}

static int cmd_factory_reset(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("wiping emulator NVS namespaces…\n");
    const char *namespaces[] = {
        "niv_num",     // HR numerical store
        "niv_alpha",   // HA alpha store
        "niv_consum",  // consumables + parts
        "niv_fam",     // last-selected family
    };
    for (size_t i = 0; i < sizeof(namespaces)/sizeof(namespaces[0]); i++) {
        nvs_handle_t h;
        if (nvs_open(namespaces[i], NVS_READWRITE, &h) == ESP_OK) {
            nvs_erase_all(h);
            nvs_commit(h);
            nvs_close(h);
            printf("  cleared %s\n", namespaces[i]);
        }
    }
    printf("rebooting in 1s…\n");
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
    return 0;  // unreachable
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

    // tank <name> <pct>
    s_tank_args.name = arg_str1(NULL, NULL, "<name>", "water|beans|tray|filter");
    s_tank_args.pct  = arg_int1(NULL, NULL, "<pct>",  "0..100");
    s_tank_args.end  = arg_end(3);
    const esp_console_cmd_t c_tank = {
        .command = "tank", .help = "Set consumable level (Phase D)",
        .func = cmd_tank, .argtable = &s_tank_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_tank));

    // fix <name>
    s_fix_args.name = arg_str1(NULL, NULL, "<name>", "water|beans|tray|filter|all|brew_unit|trays|powder_lid");
    s_fix_args.end  = arg_end(2);
    const esp_console_cmd_t c_fix = {
        .command = "fix", .help = "Refill / re-seat (Phase D)",
        .func = cmd_fix, .argtable = &s_fix_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_fix));

    // part <name> <on|off>
    s_part_args.name  = arg_str1(NULL, NULL, "<name>",  "brew_unit|trays|powder_lid");
    s_part_args.state = arg_str1(NULL, NULL, "<state>", "on|off");
    s_part_args.end   = arg_end(3);
    const esp_console_cmd_t c_part = {
        .command = "part", .help = "Set mechanical part present/absent (Phase D)",
        .func = cmd_part, .argtable = &s_part_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_part));

    const esp_console_cmd_t c_tanks = {
        .command = "tanks", .help = "Dump consumables / parts (Phase D)",
        .func = cmd_tanks_dump,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_tanks));

    const esp_console_cmd_t c_factory = {
        .command = "factory_reset",
        .help = "Wipe emulator NVS (consumables/family/store) + reboot",
        .func = cmd_factory_reset,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_factory));

    // maint <cycle>
    s_maint_args.name = arg_str1(NULL, NULL, "<cycle>",
        "descale|easy_clean|intensive_clean|filter_insert|filter_replace|filter_remove|evaporating|rinse");
    s_maint_args.end = arg_end(2);
    const esp_console_cmd_t c_maint = {
        .command = "maint",
        .help = "Start a maintenance cycle (Phase E)",
        .func = cmd_maint, .argtable = &s_maint_args,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_maint));

    const esp_console_cmd_t c_stats = {
        .command = "stats",
        .help = "Dump HR stat counters (Phase B)",
        .func = cmd_stats,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&c_stats));

    ESP_ERROR_CHECK(esp_console_register_help_command());
    ESP_ERROR_CHECK(esp_console_start_repl(repl));
    ESP_LOGI(TAG, "CLI ready (type 'help')");
}
