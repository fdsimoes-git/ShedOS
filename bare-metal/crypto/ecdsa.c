#include "ecdsa.h"
#include <string.h>

/* Fixed-size big-number modular arithmetic + Jacobian EC point math.
 * Capacity 24 limbs (768 bits) — comfortably holds 384-bit operands and
 * their 768-bit products. Generic bit-by-bit reduction (no curve-specific
 * fast path): correct and simple; verification runs once per cert, so the
 * cost is acceptable. */

#define NL 24
typedef uint32_t bn[NL];

static void bn_zero(bn r) { for (int i = 0; i < NL; i++) r[i] = 0; }
static void bn_copy(bn r, const bn a) { for (int i = 0; i < NL; i++) r[i] = a[i]; }
static int  bn_is_zero(const bn a) { for (int i = 0; i < NL; i++) if (a[i]) return 0; return 1; }

static int bn_cmp(const bn a, const bn b) {
    for (int i = NL - 1; i >= 0; i--) {
        if (a[i] < b[i]) return -1;
        if (a[i] > b[i]) return 1;
    }
    return 0;
}

static int bn_add(bn r, const bn a, const bn b) {   /* returns carry */
    uint64_t c = 0;
    for (int i = 0; i < NL; i++) { uint64_t s = (uint64_t)a[i] + b[i] + c; r[i] = (uint32_t)s; c = s >> 32; }
    return (int)c;
}
static void bn_sub(bn r, const bn a, const bn b) {  /* assumes a >= b */
    int64_t borrow = 0;
    for (int i = 0; i < NL; i++) {
        int64_t d = (int64_t)a[i] - b[i] - borrow;
        if (d < 0) { d += 0x100000000LL; borrow = 1; } else borrow = 0;
        r[i] = (uint32_t)d;
    }
}
static void bn_shl1(bn r) {
    uint32_t c = 0;
    for (int i = 0; i < NL; i++) { uint32_t nc = r[i] >> 31; r[i] = (r[i] << 1) | c; c = nc; }
}

static void bn_from_bytes(bn r, const uint8_t *b, int len) {
    bn_zero(r);
    for (int i = 0; i < len; i++) {
        int bytepos = len - 1 - i;          /* big-endian input */
        r[i / 4] |= (uint32_t)b[bytepos] << ((i % 4) * 8);
    }
}

static int hexval(char c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }
static void bn_from_hex(bn r, const char *h) {
    uint8_t tmp[48]; int n = 0;
    for (const char *p = h; p[0] && p[1]; p += 2) tmp[n++] = (hexval(p[0]) << 4) | hexval(p[1]);
    bn_from_bytes(r, tmp, n);
}

/* rem = a (alimbs limbs) mod m, bit-by-bit. */
static void bn_mod_buf(bn rem, const uint32_t *a, int alimbs, const bn m) {
    bn r; bn_zero(r);
    for (int i = alimbs * 32 - 1; i >= 0; i--) {
        bn_shl1(r);
        r[0] |= (a[i / 32] >> (i % 32)) & 1;
        if (bn_cmp(r, m) >= 0) bn_sub(r, r, m);
    }
    bn_copy(rem, r);
}

static void bn_mulmod(bn r, const bn a, const bn b, const bn m) {
    uint32_t t[2 * NL];
    for (int i = 0; i < 2 * NL; i++) t[i] = 0;
    for (int i = 0; i < NL; i++) {
        uint64_t carry = 0;
        for (int j = 0; j < NL; j++) {
            uint64_t cur = (uint64_t)t[i + j] + (uint64_t)a[i] * b[j] + carry;
            t[i + j] = (uint32_t)cur; carry = cur >> 32;
        }
        t[i + NL] += (uint32_t)carry;
    }
    bn_mod_buf(r, t, 2 * NL, m);
}

static void bn_addmod(bn r, const bn a, const bn b, const bn m) {
    bn_add(r, a, b);
    if (bn_cmp(r, m) >= 0) bn_sub(r, r, m);
}
static void bn_submod(bn r, const bn a, const bn b, const bn m) {
    if (bn_cmp(a, b) >= 0) bn_sub(r, a, b);
    else { bn t; bn_add(t, a, m); bn_sub(r, t, b); }
}

/* r = a^e mod m (square-and-multiply). */
static void bn_modexp(bn r, const bn a, const bn e, const bn m) {
    bn result; bn_zero(result); result[0] = 1;
    int top = NL * 32 - 1;
    while (top > 0 && !((e[top / 32] >> (top % 32)) & 1)) top--;
    for (int i = top; i >= 0; i--) {
        bn_mulmod(result, result, result, m);
        if ((e[i / 32] >> (i % 32)) & 1) bn_mulmod(result, result, a, m);
    }
    bn_copy(r, result);
}
static void bn_modinv(bn r, const bn a, const bn m) {   /* Fermat: a^(m-2) */
    bn two, e; bn_zero(two); two[0] = 2; bn_sub(e, m, two); bn_modexp(r, a, e, m);
}

/* ── Curve + Jacobian point math (y^2 = x^3 - 3x + b) ──────────────────────── */
typedef struct { bn p, n, b, gx, gy; int bytelen; } curve_t;
typedef struct { bn X, Y, Z; } jpoint;   /* Z==0 => point at infinity */

static void pt_double(jpoint *o, const jpoint *q, const curve_t *c) {
    if (bn_is_zero(q->Z)) { *o = *q; return; }
    bn delta, gamma, beta, alpha, t1, t2, three;
    bn_zero(three); three[0] = 3;
    bn_mulmod(delta, q->Z, q->Z, c->p);            /* Z^2 */
    bn_mulmod(gamma, q->Y, q->Y, c->p);            /* Y^2 */
    bn_mulmod(beta, q->X, gamma, c->p);            /* X*gamma */
    bn_submod(t1, q->X, delta, c->p);
    bn_addmod(t2, q->X, delta, c->p);
    bn_mulmod(t1, t1, t2, c->p);
    bn_mulmod(alpha, t1, three, c->p);             /* 3*(X-d)*(X+d) */
    bn X3, Z3, Y3, eight, four, t3;
    bn_zero(eight); eight[0] = 8; bn_zero(four); four[0] = 4;
    bn_mulmod(X3, alpha, alpha, c->p);
    bn_mulmod(t3, beta, eight, c->p);
    bn_submod(X3, X3, t3, c->p);                   /* alpha^2 - 8beta */
    bn_addmod(Z3, q->Y, q->Z, c->p);
    bn_mulmod(Z3, Z3, Z3, c->p);
    bn_submod(Z3, Z3, gamma, c->p);
    bn_submod(Z3, Z3, delta, c->p);                /* (Y+Z)^2 - g - d */
    bn_mulmod(t3, beta, four, c->p);
    bn_submod(Y3, t3, X3, c->p);
    bn_mulmod(Y3, Y3, alpha, c->p);
    bn_mulmod(t3, gamma, gamma, c->p);
    bn_mulmod(t3, t3, eight, c->p);
    bn_submod(Y3, Y3, t3, c->p);                   /* alpha*(4beta-X3) - 8gamma^2 */
    bn_copy(o->X, X3); bn_copy(o->Y, Y3); bn_copy(o->Z, Z3);
}

static void pt_add(jpoint *o, const jpoint *p1, const jpoint *p2, const curve_t *c) {
    if (bn_is_zero(p1->Z)) { *o = *p2; return; }
    if (bn_is_zero(p2->Z)) { *o = *p1; return; }
    bn Z1Z1, Z2Z2, U1, U2, S1, S2, t;
    bn_mulmod(Z1Z1, p1->Z, p1->Z, c->p);
    bn_mulmod(Z2Z2, p2->Z, p2->Z, c->p);
    bn_mulmod(U1, p1->X, Z2Z2, c->p);
    bn_mulmod(U2, p2->X, Z1Z1, c->p);
    bn_mulmod(S1, p1->Y, p2->Z, c->p); bn_mulmod(S1, S1, Z2Z2, c->p);
    bn_mulmod(S2, p2->Y, p1->Z, c->p); bn_mulmod(S2, S2, Z1Z1, c->p);
    if (bn_cmp(U1, U2) == 0) {
        if (bn_cmp(S1, S2) != 0) { bn_zero(o->X); bn_zero(o->Y); bn_zero(o->Z); return; }
        pt_double(o, p1, c); return;
    }
    bn H, I, J, r, V, X3, Y3, Z3, two; bn_zero(two); two[0] = 2;
    bn_submod(H, U2, U1, c->p);
    bn_addmod(I, H, H, c->p); bn_mulmod(I, I, I, c->p);   /* (2H)^2 */
    bn_mulmod(J, H, I, c->p);
    bn_submod(r, S2, S1, c->p); bn_addmod(r, r, r, c->p); /* 2(S2-S1) */
    bn_mulmod(V, U1, I, c->p);
    bn_mulmod(X3, r, r, c->p); bn_submod(X3, X3, J, c->p);
    bn_addmod(t, V, V, c->p); bn_submod(X3, X3, t, c->p); /* r^2 - J - 2V */
    bn_submod(Y3, V, X3, c->p); bn_mulmod(Y3, Y3, r, c->p);
    bn_mulmod(t, S1, J, c->p); bn_addmod(t, t, t, c->p);
    bn_submod(Y3, Y3, t, c->p);                          /* r(V-X3) - 2 S1 J */
    bn_addmod(Z3, p1->Z, p2->Z, c->p); bn_mulmod(Z3, Z3, Z3, c->p);
    bn_submod(Z3, Z3, Z1Z1, c->p); bn_submod(Z3, Z3, Z2Z2, c->p);
    bn_mulmod(Z3, Z3, H, c->p);
    bn_copy(o->X, X3); bn_copy(o->Y, Y3); bn_copy(o->Z, Z3);
}

static void pt_mul(jpoint *o, const bn k, const jpoint *p, const curve_t *c) {
    jpoint r; bn_zero(r.X); bn_zero(r.Y); bn_zero(r.Z);   /* infinity */
    int top = NL * 32 - 1;
    while (top > 0 && !((k[top / 32] >> (top % 32)) & 1)) top--;
    for (int i = top; i >= 0; i--) {
        pt_double(&r, &r, c);
        if ((k[i / 32] >> (i % 32)) & 1) pt_add(&r, &r, p, c);
    }
    *o = r;
}

/* ── DER signature parse ───────────────────────────────────────────────────── */
static int der_sig(const uint8_t *s, int len, bn r, bn ss) {
    int o = 0;
    if (o >= len || s[o++] != 0x30) return -1;
    int seqlen = s[o++]; (void)seqlen;
    if (o >= len || s[o++] != 0x02) return -1;
    int rlen = s[o++]; if (o + rlen > len) return -1;
    bn_from_bytes(r, s + o, rlen); o += rlen;
    if (o >= len || s[o++] != 0x02) return -1;
    int slen = s[o++]; if (o + slen > len) return -1;
    bn_from_bytes(ss, s + o, slen);
    return 0;
}

/* ── verify ────────────────────────────────────────────────────────────────── */
static int verify(const curve_t *c, const uint8_t *pub,
                  const uint8_t *hash, int hashlen,
                  const uint8_t *sig, int siglen) {
    bn r, s, e, w, u1, u2, one; bn_zero(one); one[0] = 1;
    if (der_sig(sig, siglen, r, s) < 0) return -1;
    if (bn_cmp(r, one) < 0 || bn_cmp(r, c->n) >= 0) return -1;
    if (bn_cmp(s, one) < 0 || bn_cmp(s, c->n) >= 0) return -1;

    uint32_t hbuf[NL]; for (int i = 0; i < NL; i++) hbuf[i] = 0;
    bn etmp; bn_from_bytes(etmp, hash, hashlen);
    (void)hbuf;
    bn_mod_buf(e, etmp, NL, c->n);                 /* e = hash mod n */

    bn_modinv(w, s, c->n);
    bn_mulmod(u1, e, w, c->n);
    bn_mulmod(u2, r, w, c->n);

    jpoint G, Q, P1, P2, R;
    bn_copy(G.X, c->gx); bn_copy(G.Y, c->gy); bn_zero(G.Z); G.Z[0] = 1;
    bn_from_bytes(Q.X, pub, c->bytelen);
    bn_from_bytes(Q.Y, pub + c->bytelen, c->bytelen);
    bn_zero(Q.Z); Q.Z[0] = 1;

    pt_mul(&P1, u1, &G, c);
    pt_mul(&P2, u2, &Q, c);
    pt_add(&R, &P1, &P2, c);
    if (bn_is_zero(R.Z)) return -1;

    /* affine x = X / Z^2 */
    bn zinv, zinv2, x, v;
    bn_modinv(zinv, R.Z, c->p);
    bn_mulmod(zinv2, zinv, zinv, c->p);
    bn_mulmod(x, R.X, zinv2, c->p);
    bn_mod_buf(v, x, NL, c->n);                    /* x mod n */
    return bn_cmp(v, r) == 0 ? 0 : -1;
}

static curve_t P256, P384;
static int curves_ready;
static void init_curves(void) {
    if (curves_ready) return;
    bn_from_hex(P256.p,  "ffffffff00000001000000000000000000000000ffffffffffffffffffffffff");
    bn_from_hex(P256.n,  "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551");
    bn_from_hex(P256.b,  "5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b");
    bn_from_hex(P256.gx, "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296");
    bn_from_hex(P256.gy, "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5");
    P256.bytelen = 32;
    bn_from_hex(P384.p,  "fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffeffffffff0000000000000000ffffffff");
    bn_from_hex(P384.n,  "ffffffffffffffffffffffffffffffffffffffffffffffffc7634d81f4372ddf581a0db248b0a77aecec196accc52973");
    bn_from_hex(P384.b,  "b3312fa7e23ee7e4988e056be3f82d19181d9c6efe8141120314088f5013875ac656398d8a2ed19d2a85c8edd3ec2aef");
    bn_from_hex(P384.gx, "aa87ca22be8b05378eb1c71ef320ad746e1d3b628ba79b9859f741e082542a385502f25dbf55296c3a545e3872760ab7");
    bn_from_hex(P384.gy, "3617de4a96262c6f5d9e98bf9292dc29f8f41dbd289a147ce9da3113b5f0b8c00a60b1ce1d7e819d7a431d7c90ea0e5f");
    P384.bytelen = 48;
    curves_ready = 1;
}

int ecdsa_verify_p256(const uint8_t pub[64], const uint8_t *hash, int hashlen,
                      const uint8_t *sig, int siglen) {
    init_curves();
    return verify(&P256, pub, hash, hashlen, sig, siglen);
}
int ecdsa_verify_p384(const uint8_t pub[96], const uint8_t *hash, int hashlen,
                      const uint8_t *sig, int siglen) {
    init_curves();
    return verify(&P384, pub, hash, hashlen, sig, siglen);
}
