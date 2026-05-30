#pragma once
#include <stdint.h>

/* X25519 (RFC 7748). Scalars/points are 32 bytes, little-endian. */

/* out = scalar * basepoint(9) — i.e. our public key from a private scalar. */
void x25519_base(uint8_t out[32], const uint8_t scalar[32]);

/* out = scalar * point — the ECDH shared secret. */
void x25519(uint8_t out[32], const uint8_t scalar[32], const uint8_t point[32]);
