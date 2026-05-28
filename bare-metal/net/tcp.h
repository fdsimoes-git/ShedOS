#pragma once
/* TODO Phase 2c: TCP implementation */
#include <stdint.h>

typedef struct tcp_conn tcp_conn_t;

tcp_conn_t *tcp_connect(uint32_t dst_ip, uint16_t dst_port); /* blocks until ESTABLISHED */
int         tcp_send(tcp_conn_t *c, const void *buf, int len);
int         tcp_recv(tcp_conn_t *c, void *buf, int maxlen);  /* blocks */
void        tcp_close(tcp_conn_t *c);
