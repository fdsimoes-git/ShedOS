#pragma once
#include <stdint.h>
#include <stddef.h>

#define SHA256_BLOCK 64
#define SHA256_LEN   32

typedef struct {
    uint32_t state[8];
    uint64_t bitlen;
    uint8_t  buf[64];
    size_t   buflen;
} sha256_ctx;

void sha256_init(sha256_ctx *c);
void sha256_update(sha256_ctx *c, const void *data, size_t len);
void sha256_final(sha256_ctx *c, uint8_t out[32]);
void sha256(const void *data, size_t len, uint8_t out[32]);

void hmac_sha256(const uint8_t *key, size_t keylen,
                 const uint8_t *msg, size_t msglen, uint8_t out[32]);

/* HKDF (RFC 5869) */
void hkdf_extract(const uint8_t *salt, size_t saltlen,
                  const uint8_t *ikm, size_t ikmlen, uint8_t prk[32]);
void hkdf_expand(const uint8_t *prk, const uint8_t *info, size_t infolen,
                 uint8_t *out, size_t outlen);
