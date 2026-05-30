#include "tcp.h"
#include "net.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

/* Minimal single-connection TCP client, poll-mode, no retransmit queue.
 * Good enough for one request/response to api.anthropic.com over a lossless
 * QEMU NAT link. Not a general-purpose stack. */

#define F_FIN 0x01
#define F_SYN 0x02
#define F_RST 0x04
#define F_PSH 0x08
#define F_ACK 0x10

enum { CLOSED, SYN_SENT, ESTABLISHED, PEER_FIN };

#define RXBUF 16384
#define MSS   1400
#define LOCAL_PORT 49152
#define ISS   0x00C0FFEEu

struct tcp_conn {
    uint32_t remote_ip;
    uint16_t remote_port, local_port;
    uint32_t snd_nxt, snd_una;
    uint32_t rcv_nxt;
    int      state;
    uint8_t  rx[RXBUF];
    int      rx_count;             /* bytes available */
    int      rx_head, rx_tail;     /* ring indices */
};

static struct tcp_conn conn;       /* the one connection */
static int conn_active;

static void put32(uint8_t *p, uint32_t v) { p[0]=v>>24; p[1]=v>>16; p[2]=v>>8; p[3]=v; }
static uint32_t get32(const uint8_t *p) {
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}

/* TCP checksum over the IPv4 pseudo-header + segment. */
static uint16_t tcp_csum(uint32_t src, uint32_t dst, const uint8_t *seg, int len) {
    uint8_t buf[12 + 1600];
    if (len > 1600) return 0;
    put32(buf, src);
    put32(buf + 4, dst);
    buf[8] = 0;
    buf[9] = IP_PROTO_TCP;
    buf[10] = len >> 8; buf[11] = len & 0xFF;
    memcpy(buf + 12, seg, len);
    return ip_checksum(buf, 12 + len);
}

/* Build + send one segment. Advances snd_nxt by data + SYN/FIN. */
static int tcp_xmit(uint8_t flags, const uint8_t *data, int dlen) {
    uint8_t seg[20 + MSS];
    if (dlen > MSS) dlen = MSS;

    seg[0] = conn.local_port >> 8;  seg[1] = conn.local_port & 0xFF;
    seg[2] = conn.remote_port >> 8; seg[3] = conn.remote_port & 0xFF;
    put32(seg + 4, conn.snd_nxt);
    put32(seg + 8, (flags & F_ACK) ? conn.rcv_nxt : 0);
    seg[12] = 5 << 4;               /* data offset = 5 words, no options */
    seg[13] = flags;
    seg[14] = (RXBUF >> 8) & 0xFF; seg[15] = RXBUF & 0xFF;  /* window */
    seg[16] = 0; seg[17] = 0;       /* checksum */
    seg[18] = 0; seg[19] = 0;       /* urgent */
    if (dlen > 0) memcpy(seg + 20, data, dlen);

    uint16_t c = tcp_csum(g_net.ip, conn.remote_ip, seg, 20 + dlen);
    seg[16] = c >> 8; seg[17] = c & 0xFF;

    int r = ip_send(conn.remote_ip, IP_PROTO_TCP, seg, 20 + dlen);
    conn.snd_nxt += dlen + ((flags & F_SYN) ? 1 : 0) + ((flags & F_FIN) ? 1 : 0);
    return r;
}

static void rx_push(const uint8_t *data, int len) {
    for (int i = 0; i < len && conn.rx_count < RXBUF; i++) {
        conn.rx[conn.rx_head] = data[i];
        conn.rx_head = (conn.rx_head + 1) % RXBUF;
        conn.rx_count++;
    }
}

void tcp_input(uint32_t src_ip, const uint8_t *seg, int len) {
    if (!conn_active || len < 20) return;
    uint16_t sport = (seg[0] << 8) | seg[1];
    uint16_t dport = (seg[2] << 8) | seg[3];
    if (src_ip != conn.remote_ip || sport != conn.remote_port ||
        dport != conn.local_port)
        return;

    uint32_t seq = get32(seg + 4);
    uint32_t ack = get32(seg + 8);
    int      doff = (seg[12] >> 4) * 4;
    uint8_t  flags = seg[13];
    if (doff < 20 || doff > len) return;
    const uint8_t *data = seg + doff;
    int dlen = len - doff;

    if (flags & F_RST) { conn.state = CLOSED; return; }

    switch (conn.state) {
    case SYN_SENT:
        if ((flags & F_SYN) && (flags & F_ACK)) {
            conn.rcv_nxt = seq + 1;
            conn.snd_una = ack;
            conn.state   = ESTABLISHED;
            tcp_xmit(F_ACK, NULL, 0);          /* complete handshake */
        }
        break;

    case ESTABLISHED:
    case PEER_FIN:
        if (flags & F_ACK) conn.snd_una = ack;
        if (dlen > 0 && seq == conn.rcv_nxt) {
            rx_push(data, dlen);
            conn.rcv_nxt += dlen;
            tcp_xmit(F_ACK, NULL, 0);
        } else if (dlen > 0) {
            tcp_xmit(F_ACK, NULL, 0);          /* out of order: re-ACK */
        }
        if ((flags & F_FIN) && seq + dlen == conn.rcv_nxt) {
            conn.rcv_nxt += 1;
            conn.state = PEER_FIN;
            tcp_xmit(F_ACK, NULL, 0);
        }
        break;
    }
}

tcp_conn_t *tcp_connect(uint32_t dst_ip, uint16_t dst_port) {
    memset(&conn, 0, sizeof(conn));
    conn.remote_ip   = dst_ip;
    conn.remote_port = dst_port;
    conn.local_port  = LOCAL_PORT;
    conn.snd_nxt = conn.snd_una = ISS;
    conn.state   = SYN_SENT;
    conn_active  = 1;

    for (int tries = 0; tries < 6; tries++) {
        conn.snd_nxt = ISS;                    /* re-send SYN with same ISS */
        tcp_xmit(F_SYN, NULL, 0);
        for (int spin = 0; spin < 3000000; spin++) {
            net_poll();
            if (conn.state == ESTABLISHED) return &conn;
            if (conn.state == CLOSED) { printf("[tcp] connection reset\n"); return NULL; }
        }
    }
    printf("[tcp] connect timeout\n");
    conn_active = 0;
    return NULL;
}

int tcp_send(tcp_conn_t *c, const void *buf, int len) {
    (void)c;
    const uint8_t *p = buf;
    int sent = 0;
    while (sent < len) {
        if (conn.state != ESTABLISHED && conn.state != PEER_FIN) return -1;
        int chunk = len - sent > MSS ? MSS : len - sent;
        if (tcp_xmit(F_PSH | F_ACK, p + sent, chunk) < 0) return -1;
        sent += chunk;
        /* let ACKs come back so snd_una tracks */
        for (int spin = 0; spin < 500000 && conn.snd_una != conn.snd_nxt; spin++)
            net_poll();
    }
    return sent;
}

int tcp_recv(tcp_conn_t *c, void *buf, int maxlen) {
    (void)c;
    /* Block until data is available or the peer has closed and drained. */
    for (int spin = 0; conn.rx_count == 0; spin++) {
        if (conn.state == CLOSED) return -1;
        if (conn.state == PEER_FIN && conn.rx_count == 0) return 0;  /* clean EOF */
        net_poll();
        if (spin > 40000000) return 0;          /* idle timeout */
    }
    int n = 0;
    while (n < maxlen && conn.rx_count > 0) {
        ((uint8_t *)buf)[n++] = conn.rx[conn.rx_tail];
        conn.rx_tail = (conn.rx_tail + 1) % RXBUF;
        conn.rx_count--;
    }
    return n;
}

void tcp_close(tcp_conn_t *c) {
    (void)c;
    if (conn.state == ESTABLISHED || conn.state == PEER_FIN)
        tcp_xmit(F_FIN | F_ACK, NULL, 0);
    conn.state = CLOSED;
    conn_active = 0;
}
