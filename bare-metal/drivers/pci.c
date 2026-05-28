#include "pci.h"
#include "../lib/printf.h"
#include <stddef.h>

static inline void outl(uint16_t port, uint32_t val) {
    __asm__ volatile("outl %0, %1" : : "a"(val), "Nd"(port));
}
static inline uint32_t inl(uint16_t port) {
    uint32_t val;
    __asm__ volatile("inl %1, %0" : "=a"(val) : "Nd"(port));
    return val;
}

static uint32_t cfg_addr(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg) {
    return (1u << 31)
         | ((uint32_t)bus  << 16)
         | ((uint32_t)dev  << 11)
         | ((uint32_t)fn   <<  8)
         | (reg & 0xFC);
}

uint32_t pci_read32(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg) {
    outl(PCI_CFG_ADDR, cfg_addr(bus, dev, fn, reg));
    return inl(PCI_CFG_DATA);
}

void pci_write32(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg, uint32_t val) {
    outl(PCI_CFG_ADDR, cfg_addr(bus, dev, fn, reg));
    outl(PCI_CFG_DATA, val);
}

uint16_t pci_read16(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg) {
    uint32_t v = pci_read32(bus, dev, fn, reg & ~3);
    return (v >> ((reg & 2) * 8)) & 0xFFFF;
}

uint8_t pci_read8(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg) {
    uint32_t v = pci_read32(bus, dev, fn, reg & ~3);
    return (v >> ((reg & 3) * 8)) & 0xFF;
}

int pci_scan(pci_dev_t *table, int max) {
    int count = 0;
    for (int bus = 0; bus < 256 && count < max; bus++) {
        for (int dev = 0; dev < 32 && count < max; dev++) {
            for (int fn = 0; fn < 8 && count < max; fn++) {
                uint32_t id = pci_read32(bus, dev, fn, 0x00);
                if ((id & 0xFFFF) == 0xFFFF) {
                    if (fn == 0) break; /* no device on this slot */
                    continue;
                }
                uint32_t cls = pci_read32(bus, dev, fn, 0x08);
                table[count] = (pci_dev_t){
                    .bus     = bus, .dev = dev, .fn = fn,
                    .vendor  = id & 0xFFFF,
                    .device  = (id >> 16) & 0xFFFF,
                    .class   = (cls >> 24) & 0xFF,
                    .subclass= (cls >> 16) & 0xFF,
                };
                count++;
                /* single-function device — don't probe fn 1-7 */
                uint8_t htype = pci_read8(bus, dev, fn, 0x0E);
                if (fn == 0 && !(htype & 0x80)) break;
            }
        }
    }
    return count;
}

pci_dev_t *pci_find(pci_dev_t *table, int count, uint16_t vendor, uint16_t device) {
    for (int i = 0; i < count; i++)
        if (table[i].vendor == vendor && table[i].device == device)
            return &table[i];
    return NULL;
}

void pci_enable(pci_dev_t *dev) {
    uint16_t cmd = pci_read16(dev->bus, dev->dev, dev->fn, 0x04);
    cmd |= (1 << 2) | (1 << 1) | 1; /* bus master | MMIO | IO */
    pci_write32(dev->bus, dev->dev, dev->fn, 0x04, cmd);
}

uint64_t pci_bar(pci_dev_t *d, int idx) {
    uint32_t bar = pci_read32(d->bus, d->dev, d->fn, 0x10 + idx * 4);
    if (bar & 1) return bar & ~3;           /* I/O space */
    if ((bar >> 1 & 3) == 2) {             /* 64-bit MMIO */
        uint64_t hi = pci_read32(d->bus, d->dev, d->fn, 0x10 + (idx+1)*4);
        return (bar & ~0xF) | (hi << 32);
    }
    return bar & ~0xF;                      /* 32-bit MMIO */
}
