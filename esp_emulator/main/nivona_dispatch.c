#include "nivona_dispatch.h"
#include "nivona_frame.h"
#include "nivona_crypto.h"
#include "nivona_fsm.h"
#include "nivona_store.h"
#include "nivona_brew.h"
#include "nivona_ble.h"

#include <string.h>

#include "esp_log.h"
#include "esp_random.h"

static const char *TAG = "nivona_disp";

// Diagnostic counters visible via CLI `diag`
uint32_t g_diag_hu_rx = 0;
uint32_t g_diag_hu_ver_ok = 0;
uint32_t g_diag_hu_ver_bad = 0;
uint32_t g_diag_hu_resp = 0;
uint32_t g_diag_hx_resp = 0;
uint32_t g_diag_unhandled = 0;
uint32_t g_diag_frame_parsed = 0;

// ---- BE helpers --------------------------------------------------------

static int16_t be16_i(const uint8_t *p) {
    return (int16_t)((p[0] << 8) | p[1]);
}
static void put_be16(uint8_t *p, int16_t v) {
    p[0] = (uint8_t)((v >> 8) & 0xFF); p[1] = (uint8_t)(v & 0xFF);
}
static void put_be32(uint8_t *p, int32_t v) {
    p[0] = (uint8_t)((v >> 24) & 0xFF); p[1] = (uint8_t)((v >> 16) & 0xFF);
    p[2] = (uint8_t)((v >> 8)  & 0xFF); p[3] = (uint8_t)(v & 0xFF);
}

// ---- Response helper --------------------------------------------------
//
// Per NIVONA.md:234-238, machine-originated responses are encrypted but
// the body does NOT include the 2-byte session key — that prefix is only
// used on *requests* to prove session knowledge. So every handler calls
// this with include_key_prefix=false.
static void send_response(const char *cmd, const uint8_t *payload, size_t len) {
    nivona_frame_send(cmd, payload, len,
                      /*include_key_prefix=*/false, /*encrypt=*/true);
}

static void send_ack(void) {
    nivona_frame_send(CMD_ACK, NULL, 0,
                      /*include_key_prefix=*/false, /*encrypt=*/false);
}

static void send_nack(void) {
    nivona_frame_send(CMD_NACK, NULL, 0,
                      /*include_key_prefix=*/false, /*encrypt=*/false);
}

// ---- Handshake --------------------------------------------------------

static void handle_hu(const uint8_t *payload, size_t len) {
    g_diag_hu_rx++;
    if (len < 6) {
        ESP_LOGW(TAG, "HU too short: %u", (unsigned)len);
        return;
    }
    uint8_t expect[2];
    nivona_hu_verifier(payload, 0, 4, expect);
    if (memcmp(payload + 4, expect, 2) != 0) {
        g_diag_hu_ver_bad++;
        ESP_LOGW(TAG, "HU client verifier mismatch: got %02x%02x want %02x%02x",
                 payload[4], payload[5], expect[0], expect[1]);
        // Continue — log for visibility but don't lock out
    } else {
        g_diag_hu_ver_ok++;
    }

    uint8_t kp[2];
    esp_fill_random(kp, sizeof(kp));
    nivona_frame_mark_handshake(kp);

    // Per NIVONA.md:197-209, 218: response = echoed_seed + session_key +
    // verifier computed over response[0..5] (6 bytes: seed + key).
    uint8_t resp[8];
    memcpy(resp, payload, 4);
    memcpy(resp + 4, kp, 2);
    nivona_hu_verifier(resp, 0, 6, resp + 6);
    send_response(CMD_HANDSHAKE, resp, sizeof(resp));
    g_diag_hu_resp++;
    ESP_LOGI(TAG, "HU ok, session_key=%02x%02x", kp[0], kp[1]);
}

// ---- Status (HX) -------------------------------------------------------
//
// NIVONA.md:1192-1211: response is four int16 BE (process, sub_process,
// message, progress). Our FSM packs process/sub/info/manip/progress (mixed
// formats). To satisfy both the HA integration (parses ">hhBBh") AND the
// real Android app (parses 4×i16), we pack 8 bytes identically: the two
// middle bytes carry info+manip, which the app reads as message=i16 and
// the HA integration reads as info(u8)+manip(u8).

static void handle_hx(void) {
    uint8_t buf[8];
    nivona_fsm_pack_status(buf);
    send_response(CMD_READ_STATUS, buf, sizeof(buf));
    g_diag_hx_resp++;
}

// ---- Version (HV) — 11 bytes -------------------------------------------

static void handle_hv(void) {
    uint8_t ver[11] = {0};
    const char *s = "NIVONA v1.0";
    memcpy(ver, s, strlen(s));
    send_response(CMD_READ_VERSION, ver, sizeof(ver));
}

// ---- Serial (HL) — 20 bytes (NIVONA.md:49) -----------------------------

static void handle_hl(void) {
    uint8_t serial[20] = {0};
    // Use device name minus "NIVONA-" prefix as serial. Default fits: 15
    // chars for "8107000001-----".
    const char *name = nivona_ble_device_name();
    if (strncmp(name, "NIVONA-", 7) == 0) name += 7;
    size_t n = strlen(name);
    if (n > 20) n = 20;
    memcpy(serial, name, n);
    send_response("HL", serial, sizeof(serial));
}

// ---- Features (HI) — 10 bytes ------------------------------------------

static void handle_hi(void) {
    uint8_t feat[10] = {0};
    // NIVONA.md:1273-1309: byte 0 bit 0 = ImageTransfer. We advertise it
    // off (0x00) so the app doesn't try to push images at us.
    send_response(CMD_READ_FEATURES, feat, sizeof(feat));
}

// ---- Status frame (HS) — 10 bytes opaque (NIVONA.md:60) ----------------

static void handle_hs(void) {
    uint8_t buf[10] = {0};
    send_response("HS", buf, sizeof(buf));
}

// ---- Ping (Hp) — 24 bytes opaque (NIVONA.md:405-420) -------------------
//
// Request is "00 00". Response is 24 bytes, treated as opaque by the app
// (only the "Hp" command tag is validated).

static void handle_hp(void) {
    uint8_t buf[24] = {0};
    send_response("Hp", buf, sizeof(buf));
}

// ---- Numerical (HR / HW) ----------------------------------------------

static void handle_hr(const uint8_t *req, size_t len) {
    int16_t id = (len >= 2) ? be16_i(req) : 0;
    int32_t v = nivona_store_get_num(id);
    uint8_t out[6];
    put_be16(out, id);
    put_be32(out + 2, v);
    send_response(CMD_READ_NUMERICAL, out, sizeof(out));
}

static void handle_hw(const uint8_t *req, size_t len) {
    if (len >= 6) {
        int16_t id = be16_i(req);
        int32_t v = (int32_t)((req[2] << 24) | (req[3] << 16) |
                              (req[4] << 8)  |  req[5]);
        nivona_store_set_num(id, v);
        ESP_LOGI(TAG, "HW id=%d value=%ld", id, (long)v);
    }
    send_ack();
}

// ---- Alpha (HA / HB) ---------------------------------------------------

static void handle_ha(const uint8_t *req, size_t len) {
    int16_t id = (len >= 2) ? be16_i(req) : 0;
    uint8_t out[66] = {0};
    put_be16(out, id);
    nivona_store_get_alpha(id, out + 2, 64);
    send_response(CMD_READ_ALPHA, out, sizeof(out));
}

static void handle_hb(const uint8_t *req, size_t len) {
    if (len >= 2) {
        int16_t id = be16_i(req);
        size_t vlen = len - 2;
        if (vlen > NIVONA_ALPHA_MAX) vlen = NIVONA_ALPHA_MAX;
        nivona_store_set_alpha(id, req + 2, vlen);
        ESP_LOGI(TAG, "HB id=%d len=%u", id, (unsigned)vlen);
    }
    send_ack();
}

// ---- Recipe read (HC) — Nivona doesn't support, but stub in case ------

static void handle_hc_stub(const uint8_t *req, size_t len) {
    uint8_t out[66] = {0};
    if (len >= 2) memcpy(out, req, 2);
    send_response(CMD_READ_RECIPE, out, sizeof(out));
}

// ---- Brew (HE) + cancel (HZ) + confirm (HY) + reset (HD) --------------

static void handle_he(const uint8_t *payload, size_t len) {
    // APK-verified layout (EugsterMobileApp.decompiled.cs:6464-6524):
    //   byte[0]     = 0
    //   byte[1]     = brew_command_mode (0x04 for NIVO 8000, 0x0B others)
    //   byte[2]     = 0
    //   byte[3]     = recipe selector (JobProductParameter high byte)
    //   byte[4]     = 0
    //   byte[5]     = 0x01 for normal brew (flags/mode byte)
    //   byte[6..17] = zeros
    //
    // two_cups / temperature / strength / fluid volumes are NOT in HE —
    // they're written via HW to the machine's temporary-recipe registers
    // BEFORE the HE is issued (SendTemporaryRecipe flow, APK:5103).
    uint8_t mode = (len >= 2) ? payload[1] : 0;
    uint8_t recipe_selector = (len >= 4) ? payload[3] : 0;
    uint8_t flags = (len >= 6) ? payload[5] : 0;

    ESP_LOGI(TAG, "HE mode=0x%02x recipe=%u flags=0x%02x",
             mode, recipe_selector, flags);

    // brew_start returns false when the selector isn't in the current
    // family's recipe table (Phase C-lite) or when a brew is already
    // running. Reply NACK in both cases so the client can retry /
    // surface the error rather than silently assuming success.
    if (!nivona_brew_start((int16_t)recipe_selector, /*two_cups=*/false)) {
        ESP_LOGW(TAG, "HE rejected (selector %u not in family recipes "
                 "or brew already active)", recipe_selector);
        send_nack();
        return;
    }
    send_ack();
}

// ---- Image transfer block (HN) — NIVONA.md:1310 -----------------------

static void handle_hn(const uint8_t *payload, size_t len) {
    ESP_LOGI(TAG, "HN image block len=%u (ACKed, not stored)", (unsigned)len);
    send_ack();
}

// ---- Dispatcher --------------------------------------------------------

void nivona_dispatch(const char *cmd, const uint8_t *payload, size_t len) {
    // Reads
    if (!strcmp(cmd, CMD_HANDSHAKE))       { handle_hu(payload, len); return; }
    if (!strcmp(cmd, CMD_READ_STATUS))     { handle_hx(); return; }
    if (!strcmp(cmd, CMD_READ_VERSION))    { handle_hv(); return; }
    if (!strcmp(cmd, CMD_READ_FEATURES))   { handle_hi(); return; }
    if (!strcmp(cmd, "HL"))                { handle_hl(); return; }
    if (!strcmp(cmd, "HS"))                { handle_hs(); return; }
    if (!strcmp(cmd, "Hp"))                { handle_hp(); return; }
    if (!strcmp(cmd, CMD_READ_NUMERICAL))  { handle_hr(payload, len); return; }
    if (!strcmp(cmd, CMD_READ_ALPHA))      { handle_ha(payload, len); return; }
    if (!strcmp(cmd, CMD_READ_RECIPE))     { handle_hc_stub(payload, len); return; }

    // Writes / actions
    if (!strcmp(cmd, CMD_WRITE_NUMERICAL)) { handle_hw(payload, len); return; }
    if (!strcmp(cmd, CMD_WRITE_ALPHA))     { handle_hb(payload, len); return; }
    if (!strcmp(cmd, CMD_START_PROCESS))   { handle_he(payload, len); return; }
    if (!strcmp(cmd, CMD_CANCEL_PROCESS))  { nivona_brew_cancel(); send_ack(); return; }
    if (!strcmp(cmd, CMD_CONFIRM_PROMPT))  { nivona_fsm_set_manipulation(MANIP_NONE); send_ack(); return; }
    if (!strcmp(cmd, CMD_RESET_DEFAULT))   { send_ack(); return; }
    if (!strcmp(cmd, CMD_WRITE_RECIPE))    { send_ack(); return; }  // HC/HJ n/a for Nivona
    if (!strcmp(cmd, "HN"))                { handle_hn(payload, len); return; }

    g_diag_unhandled++;
    ESP_LOGW(TAG, "unhandled cmd=%s len=%u", cmd, (unsigned)len);
    send_ack();
}
