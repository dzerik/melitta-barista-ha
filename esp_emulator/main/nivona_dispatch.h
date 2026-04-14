#pragma once

#include <stddef.h>
#include <stdint.h>

// Process a parsed frame. `cmd` is a null-terminated 1-2 char string.
void nivona_dispatch(const char *cmd, const uint8_t *payload, size_t len);
