#pragma once
#include <stdint.h>

/* Minimal X.509 for ECDSA chains (the only kind Anthropic serves).
 * Time is encoded as a comparable integer YYYYMMDDHHMMSS. */

enum { CURVE_NONE = 0, CURVE_P256, CURVE_P384 };
enum { SIG_NONE = 0, SIG_ECDSA_SHA256, SIG_ECDSA_SHA384 };

typedef struct {
    const uint8_t *tbs;  int tbs_len;     /* signed portion (DER, incl. header) */
    const uint8_t *sig;  int sig_len;     /* signatureValue: DER ECDSA-Sig-Value */
    int sig_alg;                          /* SIG_ECDSA_SHA256 / _SHA384 */
    const uint8_t *pub;  int pub_len;     /* SPKI EC point 0x04||X||Y */
    int pub_curve;                        /* CURVE_P256 / P384 */
    uint64_t not_before, not_after;       /* YYYYMMDDHHMMSS */
    const uint8_t *san;  int san_len;     /* raw SubjectAltName SEQUENCE */
} x509_cert;

int x509_parse(const uint8_t *der, int len, x509_cert *out);

/* Verify cert->tbs signature using an issuer EC public key (point X||Y). */
int x509_verify_sig(const x509_cert *cert, const uint8_t *issuer_pub,
                    int issuer_pub_len, int issuer_curve);

/* Does the leaf's SubjectAltName cover `host`? 1 = yes. */
int x509_match_host(const x509_cert *leaf, const char *host);

/* Full validation: leaf <- intermediate <- embedded GTS Root R4, host match,
 * and (if now != 0) leaf validity window. Returns 0 on success. */
int x509_validate_chain(const uint8_t *leaf_der, int leaf_len,
                        const uint8_t *inter_der, int inter_len,
                        const char *host, uint64_t now);
