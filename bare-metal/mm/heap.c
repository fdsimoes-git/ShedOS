#include "heap.h"
#include "pmm.h"
#include "../lib/printf.h"
#include <stdint.h>
#include <string.h>

/* Simple first-fit heap with block headers.
 * Grows by allocating physical pages from the PMM and identity-mapping them
 * (we keep identity mapping throughout, so phys == virt in the first 1 GiB). */

#define HEAP_START 0x400000ULL   /* 4 MiB — above kernel + stack */
#define HEAP_MAX   0x4000000ULL  /* 64 MiB heap limit */
#define MAGIC_FREE 0xFEEDFACEUL
#define MAGIC_USED 0xDEADBEEFUL

typedef struct block {
    uint32_t      magic;
    size_t        size;   /* payload size in bytes */
    struct block *next;
    struct block *prev;
} block_t;

static block_t *head;
static uint8_t *heap_end;

static void expand(size_t min_bytes) {
    size_t pages = (min_bytes + sizeof(block_t) + 4095) / 4096 + 1;
    for (size_t i = 0; i < pages; i++) {
        uint64_t phys = pmm_alloc();
        if (!phys) { printf("[heap] OOM expanding heap\n"); return; }
        /* identity-mapped: virt == phys */
        uint8_t *page = (uint8_t *)(uintptr_t)phys;
        memset(page, 0, 4096);

        block_t *b = (block_t *)heap_end;
        b->magic = MAGIC_FREE;
        b->size  = 4096 - sizeof(block_t);
        b->next  = NULL;
        b->prev  = NULL;

        /* Coalesce with last free block if adjacent */
        block_t *last = head;
        if (!last) {
            head = b;
        } else {
            while (last->next) last = last->next;
            if (last->magic == MAGIC_FREE &&
                (uint8_t *)last + sizeof(block_t) + last->size == (uint8_t *)b) {
                last->size += sizeof(block_t) + b->size;
            } else {
                last->next = b;
                b->prev = last;
            }
        }
        heap_end += 4096;
    }
}

void heap_init(void) {
    heap_end = (uint8_t *)(uintptr_t)HEAP_START;
    head = NULL;
    expand(4096);
}

void *malloc(size_t size) {
    if (!size) return NULL;
    size = (size + 7) & ~7; /* 8-byte align */

    for (int attempt = 0; attempt < 2; attempt++) {
        for (block_t *b = head; b; b = b->next) {
            if (b->magic != MAGIC_FREE || b->size < size) continue;
            /* Split if there's room for another block header + at least 8 bytes */
            if (b->size >= size + sizeof(block_t) + 8) {
                block_t *nb = (block_t *)((uint8_t *)b + sizeof(block_t) + size);
                nb->magic = MAGIC_FREE;
                nb->size  = b->size - size - sizeof(block_t);
                nb->next  = b->next;
                nb->prev  = b;
                if (b->next) b->next->prev = nb;
                b->next = nb;
                b->size = size;
            }
            b->magic = MAGIC_USED;
            return (uint8_t *)b + sizeof(block_t);
        }
        expand(size + sizeof(block_t));
    }
    printf("[heap] malloc(%zu) failed\n", size);
    return NULL;
}

void *calloc(size_t n, size_t size) {
    void *p = malloc(n * size);
    if (p) memset(p, 0, n * size);
    return p;
}

void *realloc(void *ptr, size_t size) {
    if (!ptr) return malloc(size);
    block_t *b = (block_t *)((uint8_t *)ptr - sizeof(block_t));
    if (b->size >= size) return ptr;
    void *np = malloc(size);
    if (!np) return NULL;
    memcpy(np, ptr, b->size);
    free(ptr);
    return np;
}

void free(void *ptr) {
    if (!ptr) return;
    block_t *b = (block_t *)((uint8_t *)ptr - sizeof(block_t));
    if (b->magic != MAGIC_USED) {
        printf("[heap] double-free or corruption at %p\n", ptr);
        return;
    }
    b->magic = MAGIC_FREE;

    /* Coalesce right */
    if (b->next && b->next->magic == MAGIC_FREE) {
        b->size += sizeof(block_t) + b->next->size;
        b->next = b->next->next;
        if (b->next) b->next->prev = b;
    }
    /* Coalesce left */
    if (b->prev && b->prev->magic == MAGIC_FREE) {
        b->prev->size += sizeof(block_t) + b->size;
        b->prev->next = b->next;
        if (b->next) b->next->prev = b->prev;
    }
}
