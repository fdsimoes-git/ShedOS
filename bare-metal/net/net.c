#include "net.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

netif_t g_net;

/* ── scratch state ─────────────────────────────────────────────────────────── */
static uint8_t rxframe[2048];

#define ARP_CACHE 4
static struct { uint32_t ip; uint8_t mac[6]; int valid; } arp_cache[ARP_CACHE];

/* last received UDP datagram (consumed by dhcp/dns waiters) */
static uint8_t  udp_rx[2048];
static int      udp_rx_len;
static uint16_t udp_rx_dport;
static uint32_t udp_rx_src;
static int      udp_rx_ready;

static const uint8_t BCAST[6] = {0xff,0xff,0xff,0xff,0xff,0xff};

/* ── helpers ───────────────────────────────────────────────────────────────── */
uint16_t ip_checksum(const void *data, int len) {
    const uint8_t *p = data;
    uint32_t sum = 0;
    while (len > 1) { sum += (uint16_t)((p[0] << 8) | p[1]); p += 2; len -= 2; }
    if (len) sum += (uint16_t)(p[0] << 8);
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return (uint16_t)~sum;
}

static void ip_to_bytes(uint32_t ip, uint8_t b[4]) {
    b[0] = ip >> 24; b[1] = ip >> 16; b[2] = ip >> 8; b[3] = ip;
}
static uint32_t bytes_to_ip(const uint8_t b[4]) {
    return IP(b[0], b[1], b[2], b[3]);
}

static void arp_cache_put(uint32_t ip, const uint8_t mac[6]) {
    int slot = -1;
    for (int i = 0; i < ARP_CACHE; i++) {
        if (arp_cache[i].valid && arp_cache[i].ip == ip) { slot = i; break; }
        if (slot < 0 && !arp_cache[i].valid) slot = i;
    }
    if (slot < 0) slot = 0;
    arp_cache[slot].ip = ip;
    memcpy(arp_cache[slot].mac, mac, 6);
    arp_cache[slot].valid = 1;
}
static int arp_cache_get(uint32_t ip, uint8_t mac[6]) {
    for (int i = 0; i < ARP_CACHE; i++)
        if (arp_cache[i].valid && arp_cache[i].ip == ip) {
            memcpy(mac, arp_cache[i].mac, 6);
            return 1;
        }
    return 0;
}

static int eth_send(const uint8_t dst[6], uint16_t ethertype,
                    const void *payload, int len) {
    uint8_t frame[1600];
    if (len > (int)sizeof(frame) - 14) return -1;
    memcpy(frame, dst, 6);
    memcpy(frame + 6, g_net.mac, 6);
    frame[12] = ethertype >> 8;
    frame[13] = ethertype & 0xFF;
    memcpy(frame + 14, payload, len);
    return vnet_send(g_net.nic, frame, (uint16_t)(14 + len));
}

/* ── ARP ───────────────────────────────────────────────────────────────────── */
static void arp_send(uint16_t oper, const uint8_t target_mac[6], uint32_t target_ip) {
    uint8_t a[28];
    a[0]=0x00; a[1]=0x01;          /* htype = Ethernet */
    a[2]=0x08; a[3]=0x00;          /* ptype = IPv4 */
    a[4]=6; a[5]=4;                /* hlen, plen */
    a[6]=oper >> 8; a[7]=oper & 0xFF;
    memcpy(a + 8, g_net.mac, 6);   /* sha */
    ip_to_bytes(g_net.ip, a + 14); /* spa */
    memcpy(a + 18, target_mac, 6); /* tha */
    ip_to_bytes(target_ip, a + 24);/* tpa */
    eth_send(oper == 1 ? BCAST : target_mac, ETHERTYPE_ARP, a, 28);
}

static void arp_handle(const uint8_t *p, int len) {
    if (len < 28) return;
    uint16_t oper = (p[6] << 8) | p[7];
    uint32_t spa  = bytes_to_ip(p + 14);
    uint32_t tpa  = bytes_to_ip(p + 24);
    arp_cache_put(spa, p + 8);                  /* learn the sender */
    if (oper == 1 && tpa == g_net.ip && g_net.ip)
        arp_send(2, p + 8, spa);                /* reply to a request for us */
}

int arp_resolve(uint32_t ip, uint8_t mac_out[6]) {
    if (arp_cache_get(ip, mac_out)) return 0;
    for (int tries = 0; tries < 8; tries++) {
        arp_send(1, BCAST, ip);
        for (int spin = 0; spin < 2000000; spin++) {
            net_poll();
            if (arp_cache_get(ip, mac_out)) return 0;
        }
    }
    return -1;
}

/* ── IPv4 / UDP ────────────────────────────────────────────────────────────── */
int ip_send(uint32_t dst_ip, uint8_t proto, const void *payload, int len) {
    uint8_t nexthop[6];
    if (dst_ip == 0xFFFFFFFF) {
        memcpy(nexthop, BCAST, 6);
    } else {
        uint32_t route = ((dst_ip & g_net.netmask) == (g_net.ip & g_net.netmask))
                       ? dst_ip : g_net.gateway;
        if (arp_resolve(route, nexthop) < 0) return -1;
    }

    uint8_t pkt[1600];
    if (len > (int)sizeof(pkt) - 20) return -1;
    static uint16_t ip_id = 1;
    pkt[0] = 0x45;                 /* ver 4, IHL 5 */
    pkt[1] = 0x00;                 /* DSCP/ECN */
    uint16_t total = 20 + len;
    pkt[2] = total >> 8; pkt[3] = total & 0xFF;
    pkt[4] = ip_id >> 8; pkt[5] = ip_id & 0xFF; ip_id++;
    pkt[6] = 0x40; pkt[7] = 0x00;  /* DF, frag 0 */
    pkt[8] = 64;                   /* TTL */
    pkt[9] = proto;
    pkt[10] = 0; pkt[11] = 0;      /* checksum (filled below) */
    ip_to_bytes(g_net.ip, pkt + 12);
    ip_to_bytes(dst_ip, pkt + 16);
    uint16_t csum = ip_checksum(pkt, 20);
    pkt[10] = csum >> 8; pkt[11] = csum & 0xFF;
    memcpy(pkt + 20, payload, len);

    return eth_send(nexthop, ETHERTYPE_IPV4, pkt, total);
}

int udp_send(uint32_t dst_ip, uint16_t sport, uint16_t dport,
             const void *payload, int len) {
    uint8_t seg[1600];
    if (len > (int)sizeof(seg) - 8) return -1;
    uint16_t ulen = 8 + len;
    seg[0] = sport >> 8; seg[1] = sport & 0xFF;
    seg[2] = dport >> 8; seg[3] = dport & 0xFF;
    seg[4] = ulen >> 8;  seg[5] = ulen & 0xFF;
    seg[6] = 0; seg[7] = 0;        /* checksum 0 = not used (legal for IPv4 UDP) */
    memcpy(seg + 8, payload, len);
    return ip_send(dst_ip, IP_PROTO_UDP, seg, ulen);
}

static void ipv4_handle(const uint8_t *p, int len) {
    if (len < 20) return;
    int ihl = (p[0] & 0x0F) * 4;
    if (ihl < 20 || len < ihl) return;
    uint8_t proto = p[9];
    uint32_t src  = bytes_to_ip(p + 12);
    if (proto != IP_PROTO_UDP) return;

    const uint8_t *u = p + ihl;
    if (len - ihl < 8) return;
    uint16_t dport = (u[2] << 8) | u[3];
    uint16_t ulen  = (u[4] << 8) | u[5];
    int plen = (int)ulen - 8;
    if (plen < 0) plen = 0;
    if (plen > (int)sizeof(udp_rx)) plen = sizeof(udp_rx);

    memcpy(udp_rx, u + 8, plen);
    udp_rx_len   = plen;
    udp_rx_dport = dport;
    udp_rx_src   = src;
    udp_rx_ready = 1;
}

int net_poll(void) {
    int n = vnet_recv(g_net.nic, rxframe, sizeof(rxframe));
    if (n <= 14) return 0;
    uint16_t ethertype = (rxframe[12] << 8) | rxframe[13];
    const uint8_t *payload = rxframe + 14;
    int plen = n - 14;
    if (ethertype == ETHERTYPE_ARP)       arp_handle(payload, plen);
    else if (ethertype == ETHERTYPE_IPV4) ipv4_handle(payload, plen);
    return 1;
}

/* Wait for a UDP datagram on `dport`, copy payload to buf. Returns len or -1. */
static int udp_wait(uint16_t dport, uint8_t *buf, int maxlen, uint32_t *src_out) {
    udp_rx_ready = 0;
    for (int spin = 0; spin < 8000000; spin++) {
        net_poll();
        if (udp_rx_ready && udp_rx_dport == dport) {
            int l = udp_rx_len < maxlen ? udp_rx_len : maxlen;
            memcpy(buf, udp_rx, l);
            if (src_out) *src_out = udp_rx_src;
            udp_rx_ready = 0;
            return l;
        }
        udp_rx_ready = 0;
    }
    return -1;
}

/* ── DHCP ──────────────────────────────────────────────────────────────────── */
#define DHCP_XID 0x53484544u   /* "SHED" */

static const uint8_t *dhcp_opt(const uint8_t *opts, int len, uint8_t want, int *olen) {
    int i = 0;
    while (i < len) {
        uint8_t code = opts[i++];
        if (code == 0) continue;          /* pad */
        if (code == 255) break;           /* end */
        if (i >= len) break;
        uint8_t l = opts[i++];
        if (i + l > len) break;
        if (code == want) { if (olen) *olen = l; return &opts[i]; }
        i += l;
    }
    return NULL;
}

static int dhcp_build(uint8_t *m, uint8_t msgtype, uint32_t req_ip, uint32_t server_id) {
    memset(m, 0, 240);
    m[0] = 1;                 /* op = BOOTREQUEST */
    m[1] = 1; m[2] = 6;       /* htype Ethernet, hlen 6 */
    m[4]=(DHCP_XID>>24)&0xFF; m[5]=(DHCP_XID>>16)&0xFF; m[6]=(DHCP_XID>>8)&0xFF; m[7]=DHCP_XID&0xFF;
    m[10] = 0x80;             /* flags: broadcast */
    memcpy(m + 28, g_net.mac, 6);          /* chaddr */
    m[236]=0x63; m[237]=0x82; m[238]=0x53; m[239]=0x63;  /* magic cookie */
    int o = 240;
    m[o++]=53; m[o++]=1; m[o++]=msgtype;   /* DHCP message type */
    if (msgtype == 3) {                    /* REQUEST: requested IP + server id */
        m[o++]=50; m[o++]=4; ip_to_bytes(req_ip, m+o); o+=4;
        m[o++]=54; m[o++]=4; ip_to_bytes(server_id, m+o); o+=4;
    }
    m[o++]=55; m[o++]=3; m[o++]=1; m[o++]=3; m[o++]=6;  /* req: mask, router, dns */
    m[o++]=255;                            /* end */
    return o;
}

int dhcp_configure(void) {
    uint8_t msg[576];
    uint8_t reply[576];

    /* DISCOVER */
    int len = dhcp_build(msg, 1, 0, 0);
    if (udp_send(0xFFFFFFFF, 68, 67, msg, len) < 0) return -1;

    int rl = udp_wait(68, reply, sizeof(reply), NULL);
    if (rl < 240) { printf("[dhcp] no OFFER\n"); return -1; }
    uint32_t offered = bytes_to_ip(reply + 16);          /* yiaddr */
    const uint8_t *opts = reply + 240;
    int optlen = rl - 240, l;
    const uint8_t *sid = dhcp_opt(opts, optlen, 54, &l);
    uint32_t server_id = sid ? bytes_to_ip(sid) : 0;

    /* REQUEST */
    len = dhcp_build(msg, 3, offered, server_id);
    if (udp_send(0xFFFFFFFF, 68, 67, msg, len) < 0) return -1;

    rl = udp_wait(68, reply, sizeof(reply), NULL);
    if (rl < 240) { printf("[dhcp] no ACK\n"); return -1; }
    const uint8_t *type = dhcp_opt(reply + 240, rl - 240, 53, &l);
    if (!type || type[0] != 5) { printf("[dhcp] not ACK (type %d)\n", type?type[0]:-1); return -1; }

    g_net.ip = bytes_to_ip(reply + 16);
    const uint8_t *mask = dhcp_opt(reply + 240, rl - 240, 1, &l);
    const uint8_t *rtr  = dhcp_opt(reply + 240, rl - 240, 3, &l);
    const uint8_t *dns  = dhcp_opt(reply + 240, rl - 240, 6, &l);
    g_net.netmask = mask ? bytes_to_ip(mask) : IP(255,255,255,0);
    g_net.gateway = rtr  ? bytes_to_ip(rtr)  : 0;
    g_net.dns     = dns  ? bytes_to_ip(dns)  : 0;
    return 0;
}

void net_init(vnet_t *nic) {
    memset(&g_net, 0, sizeof(g_net));
    g_net.nic = nic;
    memcpy(g_net.mac, nic->mac, 6);
    memset(arp_cache, 0, sizeof(arp_cache));
}
