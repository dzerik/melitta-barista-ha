#pragma once

#include <stdint.h>

extern uint8_t g_own_addr_type;

void nivona_ble_start_advertising(void);
const char *nivona_ble_device_name(void);
