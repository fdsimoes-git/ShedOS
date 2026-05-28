#include "../drivers/uart.h"
#include "../drivers/pci.h"
#include "../mm/pmm.h"
#include "../mm/heap.h"
#include "../lib/printf.h"
#include "../claude/chat.h"
#include <stdint.h>

/* Populated by entry.asm: multiboot2 info physical address */
void kernel_main(uint32_t mb2_info_phys) {
    uart_init();
    printf("\n[shedos] bare-metal ShedOS x86_64 — booting\n");

    pmm_init((uint64_t)mb2_info_phys);
    heap_init();

    /* PCI scan — find virtio-net for networking */
    static pci_dev_t pci_table[64];
    int pci_count = pci_scan(pci_table, 64);
    printf("[pci] %d device(s) found\n", pci_count);

    /* Virtio-net: vendor 0x1AF4, device 0x1000 (legacy) or 0x1041 (modern) */
    pci_dev_t *nic = pci_find(pci_table, pci_count, 0x1AF4, 0x1000);
    if (!nic) nic = pci_find(pci_table, pci_count, 0x1AF4, 0x1041);
    if (nic) {
        printf("[pci] virtio-net at %02x:%02x.%x\n", nic->bus, nic->dev, nic->fn);
        pci_enable(nic);
    } else {
        printf("[pci] WARNING: no virtio-net found — network unavailable\n");
    }

    /* Hand off to the chat subsystem (DHCP → DNS → TLS → API → REPL) */
    chat_run(nic);

    /* Should never return */
    printf("[shedos] chat_run() returned — halting\n");
    for (;;) __asm__("hlt");
}
