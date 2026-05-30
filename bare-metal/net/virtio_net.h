#pragma once
/* TODO Phase 2b: virtio-net driver (split virtqueue, poll-mode TX/RX) */
#include "../drivers/pci.h"
#include <stdint.h>

typedef struct {
    uint8_t mac[6];
    /* opaque driver state */
} vnet_t;

int     vnet_init(pci_dev_t *dev, vnet_t *out);
int     vnet_send(vnet_t *v, const void *pkt, uint16_t len);
int     vnet_recv(vnet_t *v, void *buf, uint16_t maxlen); /* returns bytes or 0 */
