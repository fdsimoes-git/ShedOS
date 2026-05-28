#pragma once
#include <stdint.h>

/* PCI config space (type-1 I/O port access) */
#define PCI_CFG_ADDR 0xCF8
#define PCI_CFG_DATA 0xCFC

uint32_t pci_read32(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg);
void     pci_write32(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg, uint32_t val);
uint16_t pci_read16(uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg);
uint8_t  pci_read8 (uint8_t bus, uint8_t dev, uint8_t fn, uint8_t reg);

typedef struct {
    uint8_t  bus, dev, fn;
    uint16_t vendor, device;
    uint8_t  class, subclass;
} pci_dev_t;

/* Scan bus 0 and fill the table; returns number of devices found. */
int pci_scan(pci_dev_t *table, int max);

/* Return a pointer into the table for the first matching vendor+device, or NULL. */
pci_dev_t *pci_find(pci_dev_t *table, int count, uint16_t vendor, uint16_t device);

/* Enable bus-master DMA and MMIO decode for a device. */
void pci_enable(pci_dev_t *dev);

/* Read the BAR at index bar_idx (0-5); returns the base address. */
uint64_t pci_bar(pci_dev_t *dev, int bar_idx);
