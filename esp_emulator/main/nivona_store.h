#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define NIVONA_ALPHA_MAX 64

void nivona_store_init(void);

// Numerical registers (HR/HW): int16 id → int32 value
int32_t nivona_store_get_num(int16_t id);
void    nivona_store_set_num(int16_t id, int32_t value);
bool    nivona_store_has_num(int16_t id);

// Alphanumeric registers (HA/HB): int16 id → up to 64 bytes UTF-8
// Returns number of bytes written into out (0..NIVONA_ALPHA_MAX).
size_t  nivona_store_get_alpha(int16_t id, uint8_t *out, size_t max);
void    nivona_store_set_alpha(int16_t id, const uint8_t *data, size_t len);

// Dump all known registers to log (for CLI debugging).
void    nivona_store_dump(void);
