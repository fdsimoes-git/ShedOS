#pragma once
#include <stdint.h>

/* ECDSA signature verification over NIST P-256 and P-384.
 * pub = uncompressed coordinates X||Y (no 0x04 prefix): 64 or 96 bytes.
 * sig = DER-encoded SEQUENCE { INTEGER r, INTEGER s }.
 * hash = message digest (32 bytes for P-256, 48 for P-384).
 * Returns 0 if the signature is valid, -1 otherwise. */

int ecdsa_verify_p256(const uint8_t pub[64], const uint8_t *hash, int hashlen,
                      const uint8_t *sig, int siglen);
int ecdsa_verify_p384(const uint8_t pub[96], const uint8_t *hash, int hashlen,
                      const uint8_t *sig, int siglen);
