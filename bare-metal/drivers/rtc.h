#pragma once
#include <stdint.h>

/* Current wall-clock time from the CMOS RTC, as YYYYMMDDHHMMSS (UTC).
 * Matches the encoding used by x509 validity comparisons. */
uint64_t rtc_now(void);
