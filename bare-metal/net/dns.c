#include "dns.h"
#include "net.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

/* Minimal DNS A-record resolver over UDP (RFC 1035), one question per query.
 * Sends to the DHCP-supplied resolver (g_net.dns). */

#define DNS_PORT  53
#define DNS_SPORT 50000
#define DNS_ID    0x5345        /* "SE" */

/* Encode "api.anthropic.com" as length-prefixed labels into out. Returns len. */
static int encode_qname(const char *host, uint8_t *out, int max) {
    int o = 0;
    const char *p = host;
    while (*p) {
        const char *seg = p;
        int len = 0;
        while (seg[len] && seg[len] != '.') len++;
        if (len == 0 || len > 63 || o + len + 1 >= max) return -1;
        out[o++] = (uint8_t)len;
        for (int i = 0; i < len; i++) out[o++] = (uint8_t)seg[i];
        p = seg + len;
        if (*p == '.') p++;
    }
    if (o + 1 >= max) return -1;
    out[o++] = 0;               /* root label */
    return o;
}

/* Advance past a (possibly compressed) name at offset `off` in msg[0..len).
 * Returns the offset just after the name, or -1 on malformation. */
static int skip_name(const uint8_t *msg, int len, int off) {
    while (off < len) {
        uint8_t b = msg[off];
        if ((b & 0xC0) == 0xC0) return off + 2;   /* compression pointer */
        if (b == 0) return off + 1;               /* root terminator */
        off += 1 + b;                             /* label */
    }
    return -1;
}

int dns_resolve(const char *hostname, uint32_t *out_ip) {
    if (!g_net.dns) { printf("[dns] no resolver configured\n"); return -1; }

    uint8_t q[300];
    q[0] = DNS_ID >> 8; q[1] = DNS_ID & 0xFF;
    q[2] = 0x01; q[3] = 0x00;          /* flags: recursion desired */
    q[4] = 0; q[5] = 1;                /* QDCOUNT = 1 */
    q[6] = 0; q[7] = 0;                /* ANCOUNT */
    q[8] = 0; q[9] = 0;                /* NSCOUNT */
    q[10] = 0; q[11] = 0;              /* ARCOUNT */
    int o = 12;
    int qn = encode_qname(hostname, q + o, (int)sizeof(q) - o - 4);
    if (qn < 0) return -1;
    o += qn;
    q[o++] = 0; q[o++] = 1;            /* QTYPE = A */
    q[o++] = 0; q[o++] = 1;            /* QCLASS = IN */

    if (udp_send(g_net.dns, DNS_SPORT, DNS_PORT, q, o) < 0) return -1;

    uint8_t r[1500];
    int rl = udp_recv(DNS_SPORT, r, sizeof(r), NULL);
    if (rl < 12) { printf("[dns] no response\n"); return -1; }
    if (r[0] != q[0] || r[1] != q[1]) { printf("[dns] id mismatch\n"); return -1; }
    if (r[3] & 0x0F) { printf("[dns] rcode %d\n", r[3] & 0x0F); return -1; }

    int qd = (r[4] << 8) | r[5];
    int an = (r[6] << 8) | r[7];
    int off = 12;
    for (int i = 0; i < qd; i++) {            /* skip question section */
        off = skip_name(r, rl, off);
        if (off < 0 || off + 4 > rl) return -1;
        off += 4;                             /* QTYPE + QCLASS */
    }
    for (int i = 0; i < an; i++) {
        off = skip_name(r, rl, off);
        if (off < 0 || off + 10 > rl) return -1;
        int type    = (r[off] << 8) | r[off + 1];
        int rdlen   = (r[off + 8] << 8) | r[off + 9];
        int rdata   = off + 10;
        if (rdata + rdlen > rl) return -1;
        if (type == 1 && rdlen == 4) {        /* A record */
            *out_ip = IP(r[rdata], r[rdata+1], r[rdata+2], r[rdata+3]);
            return 0;
        }
        off = rdata + rdlen;                  /* CNAME etc. — skip */
    }
    printf("[dns] no A record\n");
    return -1;
}
