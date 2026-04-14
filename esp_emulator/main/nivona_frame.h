#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define FRAME_START       0x53  // 'S'
#define FRAME_END         0x45  // 'E'
#define BLE_MTU           20
#define MAX_FRAME_BYTES   512
#define KEY_PREFIX_LEN    2

// Command codes (ASCII). Single-char A/N are unencrypted ACK/NACK.
#define CMD_ACK           "A"
#define CMD_NACK          "N"
#define CMD_HANDSHAKE     "HU"
#define CMD_READ_ALPHA    "HA"
#define CMD_WRITE_ALPHA   "HB"
#define CMD_READ_RECIPE   "HC"
#define CMD_RESET_DEFAULT "HD"
#define CMD_START_PROCESS "HE"
#define CMD_READ_FEATURES "HI"
#define CMD_WRITE_RECIPE  "HJ"
#define CMD_READ_NUMERICAL "HR"
#define CMD_READ_VERSION  "HV"
#define CMD_WRITE_NUMERICAL "HW"
#define CMD_READ_STATUS   "HX"
#define CMD_CONFIRM_PROMPT "HY"
#define CMD_CANCEL_PROCESS "HZ"

// Called by GATT layer for every chunk arriving on AD03.
void nivona_frame_feed(const uint8_t *data, size_t len);

// Reset parser state (on disconnect, on reboot).
void nivona_frame_reset(void);

// Build + chunk + notify an encrypted response frame. `cmd` is 1-2 ASCII
// bytes. If include_key_prefix is true and handshake is complete, the
// current 2-byte key_prefix is prepended to payload before encryption.
// Single-char A/N commands should pass include_key_prefix=false and
// encrypt=false.
void nivona_frame_send(const char *cmd,
                       const uint8_t *payload, size_t payload_len,
                       bool include_key_prefix,
                       bool encrypt);

// Handshake state
bool nivona_frame_handshake_complete(void);
const uint8_t *nivona_frame_key_prefix(void);  // 2 bytes; valid only after handshake
void nivona_frame_mark_handshake(const uint8_t key_prefix[2]);
