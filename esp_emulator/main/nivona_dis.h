#pragma once

// Standard Device Information Service (0x180A) — Nivona machines expose
// these during connection. The official Android app's CoffeeMachine::
// Initialize() reads them via best-effort reads (NIVONA.md:320-330).
//
// Setting per-family values affects the model displayed by the app and
// the HA integration's fallback family detection.

int nivona_dis_init(void);

// Set DIS values. Pass NULL to leave a field at its current value.
void nivona_dis_set(const char *manufacturer,
                    const char *model,
                    const char *serial,
                    const char *hw_rev,
                    const char *fw_rev,
                    const char *sw_rev);
