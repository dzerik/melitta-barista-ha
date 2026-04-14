#pragma once

#include <stdint.h>
#include <stdbool.h>

void nivona_brew_init(void);
bool nivona_brew_start(int16_t process_value, bool two_cups);
void nivona_brew_cancel(void);
bool nivona_brew_active(void);
