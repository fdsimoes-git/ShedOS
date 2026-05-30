#include "../drivers/uart.h"
#include "../drivers/pci.h"
#include "../mm/pmm.h"
#include "../mm/heap.h"
#include "../lib/printf.h"
#include "../claude/chat.h"
#include "../net/virtio_net.h"
#include "../net/net.h"
#include "../net/dns.h"
#include "../net/tcp.h"
#include "../net/tls.h"
#include <stdint.h>

/* Populated by entry.asm: multiboot2 info physical address */
void kernel_main(uint32_t mb2_info_phys) {
    uart_init();
    printf("\n[shedos] bare-metal ShedOS x86_64 — booting\n");

    pmm_init((uint64_t)mb2_info_phys);
    heap_init();

    extern int crypto_selftest(void);
    crypto_selftest();

    /* PCI scan — find virtio-net for networking */
    static pci_dev_t pci_table[64];
    int pci_count = pci_scan(pci_table, 64);
    printf("[pci] %d device(s) found\n", pci_count);

    /* Virtio-net: vendor 0x1AF4, device 0x1000 (legacy) or 0x1041 (modern) */
    pci_dev_t *nic = pci_find(pci_table, pci_count, 0x1AF4, 0x1000);
    if (!nic) nic = pci_find(pci_table, pci_count, 0x1AF4, 0x1041);
    static vnet_t vnet;
    int net_up = 0;
    if (nic) {
        printf("[pci] virtio-net at %02x:%02x.%x\n", nic->bus, nic->dev, nic->fn);
        pci_enable(nic);
        if (vnet_init(nic, &vnet) == 0) {
            net_init(&vnet);
            if (dhcp_configure() == 0) {
                printf("[net] DHCP: ip=%u.%u.%u.%u gw=%u.%u.%u.%u dns=%u.%u.%u.%u\n",
                       (g_net.ip>>24)&0xFF,(g_net.ip>>16)&0xFF,(g_net.ip>>8)&0xFF,g_net.ip&0xFF,
                       (g_net.gateway>>24)&0xFF,(g_net.gateway>>16)&0xFF,(g_net.gateway>>8)&0xFF,g_net.gateway&0xFF,
                       (g_net.dns>>24)&0xFF,(g_net.dns>>16)&0xFF,(g_net.dns>>8)&0xFF,g_net.dns&0xFF);
                uint8_t gwmac[6];
                if (arp_resolve(g_net.gateway, gwmac) == 0)
                    printf("[net] gateway MAC %02x:%02x:%02x:%02x:%02x:%02x\n",
                           gwmac[0],gwmac[1],gwmac[2],gwmac[3],gwmac[4],gwmac[5]);
                uint32_t apiip = 0;
                if (dns_resolve("api.anthropic.com", &apiip) == 0) {
                    printf("[dns] api.anthropic.com -> %u.%u.%u.%u\n",
                           (apiip>>24)&0xFF,(apiip>>16)&0xFF,(apiip>>8)&0xFF,apiip&0xFF);
                    (void)apiip;
                } else {
                    printf("[dns] resolve failed\n");
                }
                net_up = 1;
            } else {
                printf("[net] DHCP failed\n");
            }
        }
    } else {
        printf("[pci] WARNING: no virtio-net found — network unavailable\n");
    }
    (void)net_up;

    /* Hand off to the chat subsystem (DHCP → DNS → TLS → API → REPL) */
    chat_run(nic);

    /* Should never return */
    printf("[shedos] chat_run() returned — halting\n");
    for (;;) __asm__("hlt");
}
