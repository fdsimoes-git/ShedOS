#pragma once
#include "virtio_net.h"
#include <stdint.h>

/* Minimal IPv4 stack: Ethernet + ARP + IPv4 + UDP, poll-mode over virtio-net.
 * Single interface, single ARP entry (the gateway) — we only ever talk to
 * one upstream host (api.anthropic.com) through the NAT gateway. */

/* Byte-order helpers (x86 is little-endian). */
static inline uint16_t htons(uint16_t x) { return (uint16_t)((x << 8) | (x >> 8)); }
static inline uint16_t ntohs(uint16_t x) { return htons(x); }
static inline uint32_t htonl(uint32_t x) {
    return ((x & 0xFF) << 24) | ((x & 0xFF00) << 8) |
           ((x >> 8) & 0xFF00) | ((x >> 24) & 0xFF);
}
static inline uint32_t ntohl(uint32_t x) { return htonl(x); }

#define IP(a,b,c,d) (((uint32_t)(a)<<24)|((uint32_t)(b)<<16)|((uint32_t)(c)<<8)|(uint32_t)(d))

#define ETHERTYPE_ARP  0x0806
#define ETHERTYPE_IPV4 0x0800
#define IP_PROTO_UDP   17
#define IP_PROTO_TCP   6

typedef struct {
    vnet_t  *nic;
    uint8_t  mac[6];
    uint32_t ip;          /* host byte order */
    uint32_t gateway;
    uint32_t netmask;
    uint32_t dns;
    uint8_t  gw_mac[6];
    int      gw_mac_valid;
} netif_t;

extern netif_t g_net;

void net_init(vnet_t *nic);

/* Pump one received frame through the stack (ARP replies, etc.).
 * Returns 1 if a frame was processed, 0 if the RX ring was empty. */
int  net_poll(void);

/* Internet checksum (RFC 1071) over `len` bytes. */
uint16_t ip_checksum(const void *data, int len);

/* Resolve `ip` to a MAC (blocking, with timeout). 0 = ok, -1 = fail. */
int  arp_resolve(uint32_t ip, uint8_t mac_out[6]);

/* Send an IPv4 packet (routes through the gateway if off-subnet). */
int  ip_send(uint32_t dst_ip, uint8_t proto, const void *payload, int len);

/* Send a UDP datagram. */
int  udp_send(uint32_t dst_ip, uint16_t sport, uint16_t dport,
              const void *payload, int len);

/* Block (with timeout) for a UDP datagram addressed to local port `dport`.
 * Copies payload into buf, returns length or -1 on timeout. */
int  udp_recv(uint16_t dport, void *buf, int maxlen, uint32_t *src_out);

/* DHCP: acquire ip/gateway/netmask/dns from the network. 0 = ok, -1 = fail. */
int  dhcp_configure(void);
