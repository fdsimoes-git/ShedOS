#pragma once
/* Minimal single-connection TCP client (poll-mode). */
#include <stdint.h>

typedef struct tcp_conn tcp_conn_t;

tcp_conn_t *tcp_connect(uint32_t dst_ip, uint16_t dst_port); /* blocks until ESTABLISHED */
int         tcp_send(tcp_conn_t *c, const void *buf, int len);
int         tcp_recv(tcp_conn_t *c, void *buf, int maxlen);  /* blocks; 0 = peer closed */
void        tcp_close(tcp_conn_t *c);

/* Fed by the IPv4 layer for every received TCP segment. */
void        tcp_input(uint32_t src_ip, const uint8_t *seg, int len);
