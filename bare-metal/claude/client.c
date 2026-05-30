#include "client.h"
#include "session.h"
#include "../http/client.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

#ifndef CLAUDE_TOKEN
#define CLAUDE_TOKEN ""
#endif
static const char TOKEN[] = CLAUDE_TOKEN;

#define MODEL "claude-opus-4-6"
#define SYSTEM_PROMPT \
    "You are Claude Code, Anthropic's official CLI for Claude.\n\n" \
    "You are running as the entire userspace of ShedOS, a bare-metal x86-64 " \
    "operating system written from scratch in C and assembly. There is no OS " \
    "beneath you: the kernel brings up virtio-net, speaks TCP/IP, and performs " \
    "a TLS 1.3 handshake (with full certificate validation) to reach this API " \
    "directly. Be concise."

int claude_has_token(void) { return TOKEN[0] != '\0'; }

static int append_lit(char *dst, int o, const char *s) {
    int i = 0; while (s[i]) dst[o++] = s[i++]; return o;
}

/* Append `s` to dst[o], JSON-escaping. Returns new offset. */
static int json_str(char *dst, int o, const char *s, int len) {
    for (int i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"':  dst[o++]='\\'; dst[o++]='"'; break;
        case '\\': dst[o++]='\\'; dst[o++]='\\'; break;
        case '\n': dst[o++]='\\'; dst[o++]='n'; break;
        case '\r': dst[o++]='\\'; dst[o++]='r'; break;
        case '\t': dst[o++]='\\'; dst[o++]='t'; break;
        default:
            if (c < 0x20) {
                static const char hx[] = "0123456789abcdef";
                dst[o++]='\\'; dst[o++]='u'; dst[o++]='0'; dst[o++]='0';
                dst[o++]=hx[c>>4]; dst[o++]=hx[c&15];
            } else dst[o++]=c;
        }
    }
    return o;
}

/* Extract the JSON string value following the first occurrence of `key`
 * (e.g. "\"text\":") in `body`, unescaping into out. Returns 1 if found. */
static int json_find_string(const char *body, const char *key, char *out, int max) {
    const char *p = strstr(body, key);
    if (!p) return 0;
    p += strlen(key);
    while (*p == ' ') p++;
    if (*p != '"') return 0;
    p++;
    int o = 0;
    while (*p && *p != '"' && o < max - 1) {
        if (*p == '\\') {
            p++;
            switch (*p) {
            case 'n': out[o++]='\n'; break;
            case 't': out[o++]='\t'; break;
            case 'r': out[o++]='\r'; break;
            case '"': out[o++]='"'; break;
            case '\\': out[o++]='\\'; break;
            case '/': out[o++]='/'; break;
            case 'u': {
                int v = 0;
                for (int k = 0; k < 4 && p[1]; k++) {
                    char c = p[1]; int d = (c<='9')?c-'0':((c|32)-'a'+10);
                    v = v*16 + d; p++;
                }
                if (v < 0x80) out[o++] = v;
                else if (v < 0x800) { out[o++]=0xC0|(v>>6); out[o++]=0x80|(v&0x3F); }
                else { out[o++]=0xE0|(v>>12); out[o++]=0x80|((v>>6)&0x3F); out[o++]=0x80|(v&0x3F); }
                break;
            }
            default: out[o++]=*p;
            }
            p++;
        } else out[o++] = *p++;
    }
    out[o] = '\0';
    return 1;
}

static char body[200000];
static http_response_t resp;

int claude_turn(char *reply, int reply_max) {
    if (!claude_has_token()) {
        int o = 0; const char *m = "(no OAuth token baked in — rebuild with make CLAUDE_TOKEN=sk-ant-oat01-...)";
        while (m[o] && o < reply_max-1) { reply[o]=m[o]; o++; } reply[o]=0;
        return -1;
    }

    /* Build the request body from the session. */
    int o = 0;
    o = append_lit(body, o, "{\"model\":\"" MODEL "\",\"max_tokens\":8192,\"system\":\"");
    o = json_str(body, o, SYSTEM_PROMPT, (int)strlen(SYSTEM_PROMPT));
    o = append_lit(body, o, "\",\"messages\":[");
    int count; msg_t *msgs = session_messages(&count);
    for (int i = 0; i < count; i++) {
        if (i) body[o++] = ',';
        o = append_lit(body, o, "{\"role\":\"");
        o = json_str(body, o, msgs[i].role, (int)strlen(msgs[i].role));
        o = append_lit(body, o, "\",\"content\":\"");
        o = json_str(body, o, msgs[i].content, (int)strlen(msgs[i].content));
        o = append_lit(body, o, "\"}");
    }
    o = append_lit(body, o, "]}");

    static char headers[2048];
    int h = 0;
    h = append_lit(headers, h, "Authorization: Bearer ");
    h = append_lit(headers, h, TOKEN);
    h = append_lit(headers, h, "\r\nanthropic-beta: oauth-2025-04-20\r\n"
                               "anthropic-version: 2023-06-01\r\n");

    if (https_post("api.anthropic.com", "/v1/messages", headers, body, o, &resp) != 0) {
        int n = 0; const char *m = "(request failed — network or TLS error)";
        while (m[n] && n < reply_max-1) { reply[n]=m[n]; n++; } reply[n]=0;
        return -1;
    }

    if (resp.status == 200 && json_find_string(resp.body, "\"text\":", reply, reply_max))
        return 0;

    /* Non-success: surface the real server message (and status) so rate
     * limits / quota / model errors are visible rather than a generic "Error". */
    printf("[claude] HTTP %d\n", resp.status);
    if (!json_find_string(resp.body, "\"message\":", reply, reply_max)) {
        int n = 0;
        while (resp.body[n] && n < reply_max - 1) { reply[n] = resp.body[n]; n++; }
        reply[n] = 0;
    }
    return -1;
}
