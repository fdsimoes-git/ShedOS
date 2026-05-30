#pragma once
#include <stddef.h>

void  heap_init(void);
void *malloc(size_t size);
void *calloc(size_t n, size_t size);
void *realloc(void *ptr, size_t size);
void  free(void *ptr);
