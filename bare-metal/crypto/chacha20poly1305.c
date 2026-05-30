#include "chacha20poly1305.h"
#include <string.h>

/* ── ChaCha20 (RFC 8439) ───────────────────────────────────────────────────── */
#define ROL32(x,n) (((x) << (n)) | ((x) >> (32 - (n))))
#define QR(a,b,c,d) \
    a += b; d ^= a; d = ROL32(d,16); \
    c += d; b ^= c; b = ROL32(b,12); \
    a += b; d ^= a; d = ROL32(d, 8); \
    c += d; b ^= c; b = ROL32(b, 7)

static uint32_t rd32le(const uint8_t *p) {
    return p[0] | (p[1]<<8) | (p[2]<<16) | ((uint32_t)p[3]<<24);
}

static void chacha20_block(const uint8_t key[32], uint32_t counter,
                           const uint8_t nonce[12], uint8_t out[64]) {
    static const uint32_t c[4] = {0x61707865,0x3320646e,0x79622d32,0x6b206574};
    uint32_t s[16], x[16];
    s[0]=c[0]; s[1]=c[1]; s[2]=c[2]; s[3]=c[3];
    for (int i = 0; i < 8; i++) s[4+i] = rd32le(key + i*4);
    s[12] = counter;
    s[13] = rd32le(nonce); s[14] = rd32le(nonce+4); s[15] = rd32le(nonce+8);
    memcpy(x, s, sizeof(s));
    for (int i = 0; i < 10; i++) {
        QR(x[0],x[4],x[8], x[12]); QR(x[1],x[5],x[9], x[13]);
        QR(x[2],x[6],x[10],x[14]); QR(x[3],x[7],x[11],x[15]);
        QR(x[0],x[5],x[10],x[15]); QR(x[1],x[6],x[11],x[12]);
        QR(x[2],x[7],x[8], x[13]); QR(x[3],x[4],x[9], x[14]);
    }
    for (int i = 0; i < 16; i++) {
        uint32_t v = x[i] + s[i];
        out[i*4]=v; out[i*4+1]=v>>8; out[i*4+2]=v>>16; out[i*4+3]=v>>24;
    }
}

void chacha20_xor(const uint8_t key[32], uint32_t counter, const uint8_t nonce[12],
                  const uint8_t *in, uint8_t *out, size_t len) {
    uint8_t ks[64];
    size_t off = 0;
    while (off < len) {
        chacha20_block(key, counter++, nonce, ks);
        size_t n = len - off < 64 ? len - off : 64;
        for (size_t i = 0; i < n; i++) out[off+i] = in[off+i] ^ ks[i];
        off += n;
    }
}

/* ── Poly1305 (RFC 8439), TweetNaCl-style 17x8-bit limbs ───────────────────── */
static const uint32_t minusp[17] = {5,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,252};

static void add1305(uint32_t *h, const uint32_t *c) {
    uint32_t u = 0;
    for (int j = 0; j < 17; j++) { u += h[j] + c[j]; h[j] = u & 255; u >>= 8; }
}

void poly1305_mac(const uint8_t key[32], const uint8_t *m, size_t n, uint8_t out[16]) {
    uint32_t s, i, j, u, x[17], r[17], h[17], c[17], g[17];
    for (j = 0; j < 17; j++) r[j] = h[j] = 0;
    for (j = 0; j < 16; j++) r[j] = key[j];
    r[3]&=15; r[4]&=252; r[7]&=15; r[8]&=252; r[11]&=15; r[12]&=252; r[15]&=15;

    while (n > 0) {
        for (j = 0; j < 17; j++) c[j] = 0;
        for (j = 0; j < 16 && j < n; j++) c[j] = m[j];
        c[j] = 1;
        m += j; n -= j;
        u = 0; for (j = 0; j < 17; j++) { u += h[j] + c[j]; h[j] = u & 255; u >>= 8; }
        for (i = 0; i < 17; i++) {
            x[i] = 0;
            for (j = 0; j < 17; j++)
                x[i] += h[j] * ((j <= i) ? r[i-j] : 320 * r[i+17-j]);
        }
        for (i = 0; i < 17; i++) h[i] = x[i];
        u = 0; for (j = 0; j < 16; j++) { u += h[j]; h[j] = u & 255; u >>= 8; }
        u += h[16]; h[16] = u & 3; u = 5 * (u >> 2);
        for (j = 0; j < 16; j++) { u += h[j]; h[j] = u & 255; u >>= 8; }
        u += h[16]; h[16] = u;
    }
    for (j = 0; j < 17; j++) g[j] = h[j];
    add1305(h, minusp);
    s = (uint32_t)-(int32_t)(h[16] >> 7);
    for (j = 0; j < 17; j++) h[j] ^= s & (g[j] ^ h[j]);

    for (j = 0; j < 16; j++) c[j] = key[j + 16];
    c[16] = 0;
    add1305(h, c);
    for (j = 0; j < 16; j++) out[j] = h[j];
}

/* ── AEAD ──────────────────────────────────────────────────────────────────── */
static void put64le(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = v >> (8*i); }

static void poly_key(const uint8_t key[32], const uint8_t nonce[12], uint8_t pk[32]) {
    uint8_t blk[64], zero[64];
    memset(zero, 0, 64);
    chacha20_xor(key, 0, nonce, zero, blk, 64);   /* keystream block 0 */
    memcpy(pk, blk, 32);
}

/* MAC input: aad || pad16 || ct || pad16 || le64(aadlen) || le64(ctlen) */
static void aead_tag(const uint8_t pk[32], const uint8_t *aad, size_t aadlen,
                     const uint8_t *ct, size_t ctlen, uint8_t tag[16]) {
    static uint8_t mac[16384 + 64];   /* enough for our TLS records */
    size_t o = 0;
    memcpy(mac + o, aad, aadlen); o += aadlen;
    while (o % 16) mac[o++] = 0;
    memcpy(mac + o, ct, ctlen); o += ctlen;
    while (o % 16) mac[o++] = 0;
    put64le(mac + o, aadlen); o += 8;
    put64le(mac + o, ctlen);  o += 8;
    poly1305_mac(pk, mac, o, tag);
}

void aead_seal(const uint8_t key[32], const uint8_t nonce[12],
               const uint8_t *aad, size_t aadlen,
               const uint8_t *pt, size_t ptlen,
               uint8_t *ct, uint8_t tag[16]) {
    uint8_t pk[32];
    poly_key(key, nonce, pk);
    chacha20_xor(key, 1, nonce, pt, ct, ptlen);
    aead_tag(pk, aad, aadlen, ct, ptlen, tag);
}

int aead_open(const uint8_t key[32], const uint8_t nonce[12],
              const uint8_t *aad, size_t aadlen,
              const uint8_t *ct, size_t ctlen,
              const uint8_t tag[16], uint8_t *pt) {
    uint8_t pk[32], want[16];
    poly_key(key, nonce, pk);
    aead_tag(pk, aad, aadlen, ct, ctlen, want);
    uint8_t diff = 0;
    for (int i = 0; i < 16; i++) diff |= want[i] ^ tag[i];
    if (diff) return -1;
    chacha20_xor(key, 1, nonce, ct, pt, ctlen);
    return 0;
}
