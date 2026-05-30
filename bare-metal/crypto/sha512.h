#pragma once
#include <stdint.h>
#include <stddef.h>

/* SHA-512 / SHA-384 (FIPS 180-4). */

typedef struct {
    uint64_t state[8];
    uint64_t bitlen_hi, bitlen_lo;
    uint8_t  buf[128];
    size_t   buflen;
} sha512_ctx;

void sha512_init(sha512_ctx *c);
void sha384_init(sha512_ctx *c);
void sha512_update(sha512_ctx *c, const void *data, size_t len);
void sha512_final(sha512_ctx *c, uint8_t *out);   /* 64 bytes (512) */
void sha384_final(sha512_ctx *c, uint8_t out[48]);

void sha384(const void *data, size_t len, uint8_t out[48]);
