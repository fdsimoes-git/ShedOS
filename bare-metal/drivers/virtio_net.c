#include "../net/virtio_net.h"
#include "../mm/pmm.h"
#include "../lib/printf.h"
#include "pci.h"
#include <io.h>
#include <string.h>
#include <stddef.h>

/* Legacy (virtio 0.9.5 / "transitional") virtio-net over PCI I/O ports.
 *
 * We use the legacy interface deliberately: all config is via I/O ports in
 * BAR0, so we never have to map an MMIO BAR (modern virtio puts its config
 * in BARs above the 1 GiB we identity-map). Requires QEMU to expose the
 * device as legacy — see the bare-metal Makefile (-M pc + a conventional
 * PCI bus; legacy virtio is not valid on a PCIe root port).
 *
 * Poll-mode only: no interrupts, no MSI-X. queue 0 = RX, queue 1 = TX. */

/* Legacy virtio PCI I/O register offsets (no MSI-X => device config at 0x14) */
#define VIRTIO_HOST_FEATURES   0x00
#define VIRTIO_GUEST_FEATURES  0x04
#define VIRTIO_QUEUE_PFN       0x08
#define VIRTIO_QUEUE_SIZE      0x0C
#define VIRTIO_QUEUE_SEL       0x0E
#define VIRTIO_QUEUE_NOTIFY    0x10
#define VIRTIO_STATUS          0x12
#define VIRTIO_ISR             0x13
#define VIRTIO_NET_CONFIG      0x14   /* mac[6], status[2], ... */

#define VS_ACK        1
#define VS_DRIVER     2
#define VS_DRIVER_OK  4
#define VS_FAILED   128

#define VIRTIO_NET_F_MAC (1u << 5)

#define VIRTQ_DESC_F_NEXT  1
#define VIRTQ_DESC_F_WRITE 2

#define PAGE 4096
#define RX_BUFS    32
#define BUF_BYTES  2048          /* virtio_net_hdr(10) + 1514 frame, rounded up */
#define VNET_HDR_LEN 10

struct virtq_desc {
    uint64_t addr;
    uint32_t len;
    uint16_t flags;
    uint16_t next;
} __attribute__((packed));

struct virtq_avail {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[];   /* [queue size], then used_event */
} __attribute__((packed));

struct virtq_used_elem {
    uint32_t id;
    uint32_t len;
} __attribute__((packed));

struct virtq_used {
    uint16_t flags;
    uint16_t idx;
    struct virtq_used_elem ring[];  /* [queue size], then avail_event */
} __attribute__((packed));

typedef struct {
    uint16_t size;                 /* N */
    /* These rings are DMA memory shared with the device, so every access
     * must be volatile — otherwise -O2 hoists the used->idx load out of the
     * completion spin loop and we wait forever. */
    volatile struct virtq_desc  *desc;
    volatile struct virtq_avail *avail;
    volatile struct virtq_used  *used;
    uint16_t last_used;            /* our consumer index into used ring */
    uint16_t avail_idx;            /* next avail slot we will fill */
} vq_t;

static uint16_t io_base;           /* BAR0 I/O port base */
static vq_t rxq, txq;
static uint8_t *rx_buf[RX_BUFS];   /* identity-mapped buffer pointers */
static uint8_t *tx_buf;            /* single TX bounce buffer */

static inline uint16_t align_up(uint16_t bytes, uint16_t a) {
    return (bytes + a - 1) & ~(a - 1);
}

/* Allocate + lay out one split virtqueue at queue index `qsel`. */
static int vq_setup(vq_t *q, uint16_t qsel) {
    outw(io_base + VIRTIO_QUEUE_SEL, qsel);
    uint16_t n = inw(io_base + VIRTIO_QUEUE_SIZE);
    if (n == 0) { printf("[vnet] queue %u absent\n", qsel); return -1; }
    q->size = n;

    uint32_t desc_bytes  = 16u * n;
    uint32_t avail_bytes = 6u + 2u * n;                 /* flags+idx+ring[n]+used_event */
    uint32_t used_off    = (desc_bytes + avail_bytes + PAGE - 1) & ~(PAGE - 1);
    uint32_t used_bytes  = 6u + 8u * n;
    uint32_t total       = used_off + used_bytes;
    int pages = (total + PAGE - 1) / PAGE;

    uint64_t phys = pmm_alloc_pages(pages);
    if (!phys) { printf("[vnet] OOM for virtqueue (%d pages)\n", pages); return -1; }
    uint8_t *base = (uint8_t *)(uintptr_t)phys;   /* identity mapped: virt == phys */
    memset(base, 0, (size_t)pages * PAGE);

    q->desc  = (volatile struct virtq_desc  *)base;
    q->avail = (volatile struct virtq_avail *)(base + desc_bytes);
    q->used  = (volatile struct virtq_used  *)(base + used_off);
    q->last_used = 0;
    q->avail_idx = 0;

    /* Tell the device where the queue lives (page-frame number). */
    outl(io_base + VIRTIO_QUEUE_PFN, (uint32_t)(phys / PAGE));
    return 0;
}

static inline void vq_notify(uint16_t qsel) {
    outw(io_base + VIRTIO_QUEUE_NOTIFY, qsel);
}

int vnet_init(pci_dev_t *dev, vnet_t *out) {
    uint64_t bar0 = pci_bar(dev, 0);
    if (!(pci_read32(dev->bus, dev->dev, dev->fn, 0x10) & 1)) {
        printf("[vnet] BAR0 is not an I/O BAR — need legacy virtio (-M pc)\n");
        return -1;
    }
    io_base = (uint16_t)bar0;

    /* Reset, then ACK + DRIVER. */
    outb(io_base + VIRTIO_STATUS, 0);
    outb(io_base + VIRTIO_STATUS, VS_ACK);
    outb(io_base + VIRTIO_STATUS, VS_ACK | VS_DRIVER);

    /* Negotiate only VIRTIO_NET_F_MAC — keeps the RX header at 10 bytes
     * (no mergeable-rxbuf), which our recv path assumes. */
    uint32_t host = inl(io_base + VIRTIO_HOST_FEATURES);
    uint32_t guest = host & VIRTIO_NET_F_MAC;
    outl(io_base + VIRTIO_GUEST_FEATURES, guest);

    /* Read the MAC out of device config. */
    for (int i = 0; i < 6; i++)
        out->mac[i] = inb(io_base + VIRTIO_NET_CONFIG + i);

    if (vq_setup(&rxq, 0) < 0 || vq_setup(&txq, 1) < 0) {
        outb(io_base + VIRTIO_STATUS, VS_FAILED);
        return -1;
    }

    /* RX: allocate buffers, hand them all to the device. */
    for (int i = 0; i < RX_BUFS; i++) {
        uint64_t p = pmm_alloc();
        if (!p) { printf("[vnet] OOM for RX buffer\n"); return -1; }
        rx_buf[i] = (uint8_t *)(uintptr_t)p;
        rxq.desc[i].addr  = p;
        rxq.desc[i].len   = BUF_BYTES;
        rxq.desc[i].flags = VIRTQ_DESC_F_WRITE;   /* device writes into it */
        rxq.desc[i].next  = 0;
        rxq.avail->ring[i] = (uint16_t)i;
    }
    rxq.avail_idx = RX_BUFS;
    rxq.avail->idx = RX_BUFS;       /* publish */

    uint64_t tp = pmm_alloc();
    if (!tp) { printf("[vnet] OOM for TX buffer\n"); return -1; }
    tx_buf = (uint8_t *)(uintptr_t)tp;

    /* Go. */
    outb(io_base + VIRTIO_STATUS, VS_ACK | VS_DRIVER | VS_DRIVER_OK);
    vq_notify(0);   /* kick: RX buffers are available */

    printf("[vnet] up: MAC %02x:%02x:%02x:%02x:%02x:%02x  (io 0x%x, rxq=%u txq=%u)\n",
           out->mac[0], out->mac[1], out->mac[2],
           out->mac[3], out->mac[4], out->mac[5],
           io_base, rxq.size, txq.size);
    return 0;
}

int vnet_send(vnet_t *v, const void *pkt, uint16_t len) {
    (void)v;
    if (len > BUF_BYTES - VNET_HDR_LEN) return -1;

    memset(tx_buf, 0, VNET_HDR_LEN);                 /* zeroed virtio_net_hdr */
    memcpy(tx_buf + VNET_HDR_LEN, pkt, len);

    uint16_t di = 0;                                 /* reuse descriptor 0 */
    txq.desc[di].addr  = (uint64_t)(uintptr_t)tx_buf;
    txq.desc[di].len   = VNET_HDR_LEN + len;
    txq.desc[di].flags = 0;                          /* device reads */
    txq.desc[di].next  = 0;

    txq.avail->ring[txq.avail_idx % txq.size] = di;
    txq.avail_idx++;
    __asm__ volatile("" ::: "memory");
    txq.avail->idx = txq.avail_idx;
    vq_notify(1);

    /* Wait for the device to consume it (so we can reuse tx_buf). */
    for (volatile int spin = 0; txq.used->idx == txq.last_used; spin++) {
        if (spin > 50000000) { printf("[vnet] TX timeout\n"); return -1; }
    }
    txq.last_used = txq.used->idx;
    return 0;
}

int vnet_recv(vnet_t *v, void *buf, uint16_t maxlen) {
    (void)v;
    if (rxq.used->idx == rxq.last_used) return 0;     /* nothing yet */

    volatile struct virtq_used_elem *e = &rxq.used->ring[rxq.last_used % rxq.size];
    uint16_t id  = (uint16_t)e->id;
    uint32_t tot = e->len;                            /* virtio_net_hdr + frame */
    int frame_len = (int)tot - VNET_HDR_LEN;
    if (frame_len < 0) frame_len = 0;
    if (frame_len > maxlen) frame_len = maxlen;

    memcpy(buf, rx_buf[id] + VNET_HDR_LEN, frame_len);

    /* Recycle this buffer back into the avail ring. */
    rxq.desc[id].len   = BUF_BYTES;
    rxq.desc[id].flags = VIRTQ_DESC_F_WRITE;
    rxq.avail->ring[rxq.avail_idx % rxq.size] = id;
    rxq.avail_idx++;
    __asm__ volatile("" ::: "memory");
    rxq.avail->idx = rxq.avail_idx;
    rxq.last_used++;
    vq_notify(0);

    return frame_len;
}
