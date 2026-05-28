#include "pmm.h"
#include "../lib/printf.h"
#include <stddef.h>
#include <string.h>

#define PAGE_SIZE 4096ULL
#define BITMAP_MAX_FRAMES (256 * 1024)  /* covers 1 GiB */

static uint8_t  bitmap[BITMAP_MAX_FRAMES / 8];
static uint64_t total_frames;
static uint64_t free_frames;

static void mark_used(uint64_t frame) {
    if (frame < BITMAP_MAX_FRAMES)
        bitmap[frame / 8] |= (1 << (frame % 8));
}

static void mark_free(uint64_t frame) {
    if (frame < BITMAP_MAX_FRAMES) {
        bitmap[frame / 8] &= ~(1 << (frame % 8));
        free_frames++;
    }
}

static int is_used(uint64_t frame) {
    if (frame >= BITMAP_MAX_FRAMES) return 1;
    return (bitmap[frame / 8] >> (frame % 8)) & 1;
}

/* Multiboot2 memory map tag type */
#define MB2_TAG_MMAP 6
typedef struct { uint32_t type, size; } mb2_tag_hdr;
typedef struct {
    uint32_t type, size;
    uint32_t entry_size, entry_version;
} mb2_mmap_tag;
typedef struct {
    uint64_t base, len;
    uint32_t type, reserved;
} mb2_mmap_entry;
#define MB2_MMAP_AVAILABLE 1

void pmm_init(uint64_t mb2_info_phys) {
    /* Start all frames as used; we'll free usable ones below */
    memset(bitmap, 0xFF, sizeof(bitmap));
    free_frames = 0;

    /* Kernel lives at 1 MiB; reserve first 4 MiB conservatively */
    total_frames = BITMAP_MAX_FRAMES;

    /* Walk multiboot2 tags */
    uint8_t *p = (uint8_t *)(uintptr_t)(mb2_info_phys + 8); /* skip total_size + reserved */
    uint32_t total_size = *(uint32_t *)(uintptr_t)mb2_info_phys;
    uint8_t *end = (uint8_t *)(uintptr_t)mb2_info_phys + total_size;

    while (p < end) {
        mb2_tag_hdr *hdr = (mb2_tag_hdr *)p;
        if (hdr->type == 0) break; /* end tag */

        if (hdr->type == MB2_TAG_MMAP) {
            mb2_mmap_tag *mm = (mb2_mmap_tag *)p;
            mb2_mmap_entry *e = (mb2_mmap_entry *)((uint8_t *)mm + sizeof(*mm));
            mb2_mmap_entry *elim = (mb2_mmap_entry *)((uint8_t *)mm + mm->size);
            while (e < elim) {
                if (e->type == MB2_MMAP_AVAILABLE) {
                    uint64_t frame_start = (e->base + PAGE_SIZE - 1) / PAGE_SIZE;
                    uint64_t frame_end   = (e->base + e->len) / PAGE_SIZE;
                    for (uint64_t f = frame_start; f < frame_end; f++)
                        mark_free(f);
                }
                e = (mb2_mmap_entry *)((uint8_t *)e + mm->entry_size);
            }
        }

        p += (hdr->size + 7) & ~7; /* tags are 8-byte aligned */
    }

    /* Reserve first 4 MiB (BIOS data, kernel, PMM bitmap itself) */
    for (uint64_t f = 0; f < 4096ULL * 1024 / PAGE_SIZE; f++)
        mark_used(f);

    printf("[pmm] %llu MiB available\n", (unsigned long long)(free_frames * PAGE_SIZE / (1024*1024)));
}

uint64_t pmm_alloc(void) {
    for (uint64_t f = 0; f < total_frames; f++) {
        if (!is_used(f)) {
            mark_used(f);
            free_frames--;
            return f * PAGE_SIZE;
        }
    }
    return 0; /* OOM */
}

void pmm_free(uint64_t addr) {
    mark_free(addr / PAGE_SIZE);
}

uint64_t pmm_free_bytes(void) {
    return free_frames * PAGE_SIZE;
}
