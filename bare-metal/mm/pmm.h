#pragma once
#include <stdint.h>

/* Physical memory manager — bitmap allocator.
 * Call pmm_init() once with the multiboot2 memory map before any alloc. */

void     pmm_init(uint64_t mb2_info_phys);
uint64_t pmm_alloc(void);          /* returns physical frame addr, or 0 on OOM */
uint64_t pmm_alloc_pages(int n);   /* n physically-contiguous frames, or 0 on OOM */
void     pmm_free(uint64_t frame);
uint64_t pmm_free_bytes(void);
