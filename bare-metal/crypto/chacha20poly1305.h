#pragma once
#include <stdint.h>
#include <stddef.h>

/* ChaCha20-Poly1305 AEAD (RFC 8439). 96-bit nonce, 256-bit key, 128-bit tag. */

void chacha20_xor(const uint8_t key[32], uint32_t counter, const uint8_t nonce[12],
                  const uint8_t *in, uint8_t *out, size_t len);

void poly1305_mac(const uint8_t key[32], const uint8_t *msg, size_t len, uint8_t tag[16]);

/* AEAD seal: encrypt pt -> ct (same length), produce tag. */
void aead_seal(const uint8_t key[32], const uint8_t nonce[12],
               const uint8_t *aad, size_t aadlen,
               const uint8_t *pt, size_t ptlen,
               uint8_t *ct, uint8_t tag[16]);

/* AEAD open: verify tag, decrypt ct -> pt. Returns 0 on success, -1 on auth fail. */
int  aead_open(const uint8_t key[32], const uint8_t nonce[12],
               const uint8_t *aad, size_t aadlen,
               const uint8_t *ct, size_t ctlen,
               const uint8_t tag[16], uint8_t *pt);
