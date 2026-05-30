#include "x509.h"
#include "../crypto/sha256.h"
#include "../crypto/sha512.h"
#include "../crypto/ecdsa.h"
#include "../crypto/trust_anchors.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

/* ── DER ────────────────────────────────────────────────────────────────────
 * Single-byte tags only (all that X.509 certs use). der_next consumes one
 * TLV from [*p,end), returning its tag and content slice. */
static int der_next(const uint8_t **p, const uint8_t *end,
                    int *tag, const uint8_t **content, int *clen) {
    if (*p >= end) return -1;
    *tag = *(*p)++;
    if (*p >= end) return -1;
    int l = *(*p)++;
    if (l & 0x80) {
        int nb = l & 0x7f;
        if (nb == 0 || nb > 3) return -1;
        l = 0;
        while (nb--) { if (*p >= end) return -1; l = (l << 8) | *(*p)++; }
    }
    if (l < 0 || *p + l > end) return -1;
    *content = *p; *clen = l; *p += l;
    return 0;
}

static int oid_eq(const uint8_t *o, int olen, const uint8_t *want, int wlen) {
    return olen == wlen && memcmp(o, want, olen) == 0;
}
static const uint8_t OID_P256[]  = {0x2A,0x86,0x48,0xCE,0x3D,0x03,0x01,0x07};
static const uint8_t OID_P384[]  = {0x2B,0x81,0x04,0x00,0x22};
static const uint8_t OID_ESHA256[]= {0x2A,0x86,0x48,0xCE,0x3D,0x04,0x03,0x02};
static const uint8_t OID_ESHA384[]= {0x2A,0x86,0x48,0xCE,0x3D,0x04,0x03,0x03};
static const uint8_t OID_SAN[]   = {0x55,0x1D,0x11};

static int d2(const uint8_t *p) { return (p[0]-'0')*10 + (p[1]-'0'); }
static uint64_t parse_time(int tag, const uint8_t *p, int len) {
    int year, idx;
    if (tag == 0x17 && len >= 13) {          /* UTCTime YYMMDD... */
        int yy = d2(p); year = yy < 50 ? 2000 + yy : 1900 + yy; idx = 2;
    } else if (tag == 0x18 && len >= 15) {   /* GeneralizedTime YYYYMMDD... */
        year = d2(p) * 100 + d2(p + 2); idx = 4;
    } else return 0;
    int mon = d2(p+idx), day = d2(p+idx+2), hh = d2(p+idx+4), mm = d2(p+idx+6), ss = d2(p+idx+8);
    return ((((uint64_t)year*100 + mon)*100 + day)*100 + hh)*100*100
           + (uint64_t)mm*100 + ss;
}

static int parse_spki(const uint8_t *c, int clen, x509_cert *out) {
    const uint8_t *p = c, *end = c + clen, *a; int tag, alen;
    if (der_next(&p, end, &tag, &a, &alen) || tag != 0x30) return -1;   /* AlgorithmId */
    const uint8_t *ap = a, *aend = a + alen, *o; int olen;
    if (der_next(&ap, aend, &tag, &o, &olen)) return -1;               /* ecPublicKey OID */
    if (der_next(&ap, aend, &tag, &o, &olen)) return -1;               /* curve OID */
    if (oid_eq(o, olen, OID_P256, sizeof OID_P256)) out->pub_curve = CURVE_P256;
    else if (oid_eq(o, olen, OID_P384, sizeof OID_P384)) out->pub_curve = CURVE_P384;
    else return -1;
    const uint8_t *bs; int bslen;
    if (der_next(&p, end, &tag, &bs, &bslen) || tag != 0x03 || bslen < 2) return -1;
    out->pub = bs + 1; out->pub_len = bslen - 1;                       /* skip unused-bits byte */
    return 0;
}

static void parse_extensions(const uint8_t *c, int clen, x509_cert *out) {
    const uint8_t *p = c, *end = c + clen, *seq; int tag, seqlen;
    if (der_next(&p, end, &tag, &seq, &seqlen) || tag != 0x30) return;
    const uint8_t *xp = seq, *xend = seq + seqlen;
    while (xp < xend) {
        const uint8_t *ext; int extlen;
        if (der_next(&xp, xend, &tag, &ext, &extlen) || tag != 0x30) return;
        const uint8_t *ip = ext, *iend = ext + extlen, *o, *v; int olen, vlen;
        if (der_next(&ip, iend, &tag, &o, &olen)) continue;            /* extnID */
        if (der_next(&ip, iend, &tag, &v, &vlen)) continue;
        if (tag == 0x01) { if (der_next(&ip, iend, &tag, &v, &vlen)) continue; } /* critical */
        if (oid_eq(o, olen, OID_SAN, sizeof OID_SAN)) { out->san = v; out->san_len = vlen; }
    }
}

int x509_parse(const uint8_t *der, int len, x509_cert *out) {
    memset(out, 0, sizeof(*out));
    const uint8_t *p = der, *end = der + len, *c; int tag, clen;
    if (der_next(&p, end, &tag, &c, &clen) || tag != 0x30) return -1;  /* Certificate */
    const uint8_t *cp = c, *cend = c + clen;

    const uint8_t *tbs_start = cp;
    if (der_next(&cp, cend, &tag, &c, &clen) || tag != 0x30) return -1; /* tbsCertificate */
    out->tbs = tbs_start; out->tbs_len = (int)(cp - tbs_start);
    const uint8_t *tp = c, *tend = c + clen;

    const uint8_t *save = tp;
    if (der_next(&tp, tend, &tag, &c, &clen)) return -1;
    if (tag != 0xA0) tp = save;                                        /* version optional */
    if (der_next(&tp, tend, &tag, &c, &clen)) return -1;               /* serial */
    if (der_next(&tp, tend, &tag, &c, &clen)) return -1;               /* sig alg (inner) */
    if (der_next(&tp, tend, &tag, &c, &clen)) return -1;               /* issuer */
    if (der_next(&tp, tend, &tag, &c, &clen) || tag != 0x30) return -1;/* validity */
    {
        const uint8_t *vp = c, *vend = c + clen, *t; int tl;
        if (der_next(&vp, vend, &tag, &t, &tl)) return -1;
        out->not_before = parse_time(tag, t, tl);
        if (der_next(&vp, vend, &tag, &t, &tl)) return -1;
        out->not_after = parse_time(tag, t, tl);
    }
    if (der_next(&tp, tend, &tag, &c, &clen)) return -1;               /* subject */
    if (der_next(&tp, tend, &tag, &c, &clen) || tag != 0x30) return -1;/* SPKI */
    if (parse_spki(c, clen, out)) return -1;
    while (tp < tend) {                                                /* extensions [3] */
        if (der_next(&tp, tend, &tag, &c, &clen)) break;
        if (tag == 0xA3) { parse_extensions(c, clen, out); break; }
    }

    if (der_next(&cp, cend, &tag, &c, &clen) || tag != 0x30) return -1;/* signatureAlgorithm */
    {
        const uint8_t *sp = c, *send = c + clen, *o; int olen;
        if (der_next(&sp, send, &tag, &o, &olen)) return -1;
        if (oid_eq(o, olen, OID_ESHA256, sizeof OID_ESHA256)) out->sig_alg = SIG_ECDSA_SHA256;
        else if (oid_eq(o, olen, OID_ESHA384, sizeof OID_ESHA384)) out->sig_alg = SIG_ECDSA_SHA384;
        else return -1;
    }
    if (der_next(&cp, cend, &tag, &c, &clen) || tag != 0x03 || clen < 2) return -1; /* sig BIT STRING */
    out->sig = c + 1; out->sig_len = clen - 1;
    return 0;
}

/* issuer_pub is the raw EC point X||Y (no 0x04), 64 (P-256) or 96 (P-384). */
int x509_verify_sig(const x509_cert *cert, const uint8_t *issuer_pub,
                    int issuer_pub_len, int issuer_curve) {
    uint8_t h[64]; int hlen;
    if (cert->sig_alg == SIG_ECDSA_SHA256) { sha256(cert->tbs, cert->tbs_len, h); hlen = 32; }
    else if (cert->sig_alg == SIG_ECDSA_SHA384) { sha384(cert->tbs, cert->tbs_len, h); hlen = 48; }
    else return -1;
    if (issuer_curve == CURVE_P256 && issuer_pub_len == 64)
        return ecdsa_verify_p256(issuer_pub, h, hlen, cert->sig, cert->sig_len);
    if (issuer_curve == CURVE_P384 && issuer_pub_len == 96)
        return ecdsa_verify_p384(issuer_pub, h, hlen, cert->sig, cert->sig_len);
    return -1;
}

static int ci_eq(const uint8_t *a, const char *b, int n) {
    for (int i = 0; i < n; i++) {
        uint8_t x = a[i], y = (uint8_t)b[i];
        if (x >= 'A' && x <= 'Z') x += 32;
        if (y >= 'A' && y <= 'Z') y += 32;
        if (x != y) return 0;
    }
    return b[n] == '\0';
}

int x509_match_host(const x509_cert *leaf, const char *host) {
    if (!leaf->san) return 0;
    const uint8_t *p = leaf->san, *end = leaf->san + leaf->san_len, *seq; int tag, seqlen;
    if (der_next(&p, end, &tag, &seq, &seqlen) || tag != 0x30) return 0;
    const uint8_t *np = seq, *nend = seq + seqlen, *name; int nlen;
    int hostlen = (int)strlen(host);
    while (np < nend) {
        if (der_next(&np, nend, &tag, &name, &nlen)) return 0;
        if (tag != 0x82) continue;                       /* dNSName [2] IA5String */
        if (nlen == hostlen && ci_eq(name, host, nlen)) return 1;
        if (nlen > 2 && name[0] == '*' && name[1] == '.') {  /* wildcard *.x */
            const char *dot = host;
            while (*dot && *dot != '.') dot++;
            if (*dot == '.' && ci_eq(name + 1, dot, nlen - 1)) return 1;
        }
    }
    return 0;
}

int x509_validate_chain(const uint8_t *leaf_der, int leaf_len,
                        const uint8_t *inter_der, int inter_len,
                        const char *host, uint64_t now) {
    x509_cert leaf, inter;
    if (x509_parse(leaf_der, leaf_len, &leaf))  { printf("[x509] leaf parse fail\n");  return -1; }
    if (x509_parse(inter_der, inter_len, &inter)) { printf("[x509] inter parse fail\n"); return -1; }

    /* leaf signed by intermediate (strip 0x04 from intermediate's point) */
    if (x509_verify_sig(&leaf, inter.pub + 1, inter.pub_len - 1, inter.pub_curve)) {
        printf("[x509] leaf signature invalid\n"); return -1;
    }
    /* intermediate signed by embedded GTS Root R4 (P-384, X||Y already) */
    if (x509_verify_sig(&inter, GTS_R4_PUB, sizeof GTS_R4_PUB, CURVE_P384)) {
        printf("[x509] intermediate not signed by GTS Root R4\n"); return -1;
    }
    if (!x509_match_host(&leaf, host)) { printf("[x509] hostname mismatch\n"); return -1; }
    if (now) {
        if (now < leaf.not_before || now > leaf.not_after) {
            printf("[x509] leaf expired/not-yet-valid\n"); return -1;
        }
        if (now < inter.not_before || now > inter.not_after) {
            printf("[x509] intermediate expired\n"); return -1;
        }
    }
    return 0;
}
