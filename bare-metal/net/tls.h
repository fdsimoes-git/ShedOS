#pragma once
#include <stdint.h>

/* Minimal TLS 1.3 client: TLS_CHACHA20_POLY1305_SHA256 + x25519 only.
 * Single connection. Does NOT validate the server certificate (interim) —
 * see the warning printed by tls_connect(). */

typedef struct tls_conn tls_conn_t;

tls_conn_t *tls_connect(uint32_t ip, uint16_t port, const char *sni);
int         tls_send(tls_conn_t *c, const void *buf, int len);
int         tls_recv(tls_conn_t *c, void *buf, int maxlen);  /* 0 = closed */
void        tls_close(tls_conn_t *c);
