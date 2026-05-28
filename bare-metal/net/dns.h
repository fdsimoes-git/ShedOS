#pragma once
/* TODO Phase 2c: DNS stub resolver */
#include <stdint.h>

/* Resolve hostname to IPv4. Returns 0 on success, -1 on failure. */
int dns_resolve(const char *hostname, uint32_t *out_ip);
