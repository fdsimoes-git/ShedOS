#pragma once
/* TODO Phase 2d: HTTP/1.1 over TLS (mbedTLS) */
#include <stddef.h>

typedef struct {
    int  status;
    char body[65536]; /* fixed 64 KiB response buffer */
    int  body_len;
} http_response_t;

/* POST json_body to https://host/path with the given extra headers.
 * headers is a \r\n-separated list; pass NULL for none.
 * Returns 0 on success (response populated), -1 on failure. */
int https_post(const char *host, const char *path,
               const char *headers,
               const char *json_body, int json_len,
               http_response_t *resp);
