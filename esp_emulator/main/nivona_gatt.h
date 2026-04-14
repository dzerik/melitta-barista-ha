#pragma once

#include <stddef.h>
#include <stdint.h>

int  nivona_gatt_init(void);
void nivona_gatt_on_connect(uint16_t conn_handle);
void nivona_gatt_on_disconnect(void);
void nivona_gatt_on_subscribe(uint16_t attr_handle, int cur_notify);
void nivona_gatt_notify(const uint8_t *data, size_t len);
