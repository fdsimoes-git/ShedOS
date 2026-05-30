#include "client.h"
#include "../net/dns.h"
#include "../net/tls.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

/* HTTPS POST over the bare-metal TLS stack. Resolves the host, opens a TLS
 * connection, sends the request with Connection: close, reads the whole
 * response, and de-chunks the body. */

static char raw[200000];

static char lower(char c) { return (c >= 'A' && c <= 'Z') ? c + 32 : c; }

/* Case-insensitive search for `needle` in headers [s, s+n). */
static const char *find_ci(const char *s, int n, const char *needle) {
    int m = (int)strlen(needle);
    for (int i = 0; i + m <= n; i++) {
        int j = 0;
        while (j < m && lower(s[i+j]) == lower(needle[j])) j++;
        if (j == m) return s + i;
    }
    return NULL;
}

static int append(char *dst, int o, const char *s) {
    int i = 0; while (s[i]) dst[o++] = s[i++]; return o;
}
static int append_int(char *dst, int o, int v) {
    char tmp[12]; int t = 0;
    if (v == 0) tmp[t++] = '0';
    while (v > 0) { tmp[t++] = '0' + v % 10; v /= 10; }
    while (t > 0) dst[o++] = tmp[--t];
    return o;
}
static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    c = lower(c);
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

int https_post(const char *host, const char *path, const char *headers,
               const char *json_body, int json_len, http_response_t *resp) {
    uint32_t ip;
    if (dns_resolve(host, &ip) != 0) { printf("[http] DNS failed for %s\n", host); return -1; }
    tls_conn_t *t = tls_connect(ip, 443, host);
    if (!t) return -1;

    /* Build request head. */
    static char req[8192];
    int o = 0;
    o = append(req, o, "POST "); o = append(req, o, path); o = append(req, o, " HTTP/1.1\r\n");
    o = append(req, o, "Host: "); o = append(req, o, host); o = append(req, o, "\r\n");
    if (headers) o = append(req, o, headers);          /* each line \r\n-terminated */
    o = append(req, o, "Content-Type: application/json\r\n");
    o = append(req, o, "Content-Length: "); o = append_int(req, o, json_len); o = append(req, o, "\r\n");
    o = append(req, o, "Connection: close\r\n\r\n");

    if (tls_send(t, req, o) < 0 || tls_send(t, json_body, json_len) < 0) { tls_close(t); return -1; }

    /* Read the whole response. */
    int total = 0, n;
    while ((n = tls_recv(t, raw + total, (int)sizeof(raw) - 1 - total)) > 0) {
        total += n;
        if (total >= (int)sizeof(raw) - 1) break;
    }
    tls_close(t);
    raw[total] = '\0';
    if (total < 12) return -1;

    /* Status code from "HTTP/1.1 NNN". */
    resp->status = 0;
    for (int i = 9; i < 12 && raw[i] >= '0' && raw[i] <= '9'; i++)
        resp->status = resp->status * 10 + (raw[i] - '0');

    /* Split headers / body. */
    const char *bodystart = NULL;
    for (int i = 0; i + 4 <= total; i++)
        if (raw[i]=='\r'&&raw[i+1]=='\n'&&raw[i+2]=='\r'&&raw[i+3]=='\n') { bodystart = raw + i + 4; break; }
    if (!bodystart) return -1;
    int hdrlen = (int)(bodystart - raw);
    int bodylen = total - hdrlen;

    /* De-chunk if Transfer-Encoding: chunked. */
    resp->body_len = 0;
    if (find_ci(raw, hdrlen, "transfer-encoding: chunked")) {
        const char *p = bodystart, *end = raw + total;
        while (p < end) {
            int sz = 0, any = 0;
            while (p < end && hexval(*p) >= 0) { sz = sz*16 + hexval(*p); p++; any = 1; }
            while (p < end && *p != '\n') p++;            /* to end of size line */
            if (p < end) p++;
            if (!any || sz == 0) break;
            for (int i = 0; i < sz && p < end && resp->body_len < (int)sizeof(resp->body)-1; i++)
                resp->body[resp->body_len++] = *p++;
            if (p + 2 <= end) p += 2;                     /* trailing CRLF */
        }
    } else {
        int cap = (int)sizeof(resp->body) - 1;
        int n2 = bodylen < cap ? bodylen : cap;
        memcpy(resp->body, bodystart, n2);
        resp->body_len = n2;
    }
    resp->body[resp->body_len] = '\0';
    return 0;
}
