#pragma once

#include <stddef.h>
#include <stdint.h>

#define NIVONA_RC4_KEY_LEN 32

extern const uint8_t NIVONA_RC4_KEY[NIVONA_RC4_KEY_LEN];
extern const uint8_t NIVONA_HU_TABLE[256];

// Symmetric RC4 stream cipher (in-place safe, i.e. out may equal in)
void nivona_rc4(const uint8_t *key, size_t key_len,
                const uint8_t *in, uint8_t *out, size_t len);

// 2-round Nivona HU verifier over buf[start..start+count), writes 2 bytes
// into out[0..1].
void nivona_hu_verifier(const uint8_t *buf, size_t start, size_t count,
                        uint8_t out[2]);
