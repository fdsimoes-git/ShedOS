#include "rtc.h"
#include <io.h>

static uint8_t cmos(int reg) {
    outb(0x70, (uint8_t)reg);
    return inb(0x71);
}

uint64_t rtc_now(void) {
    /* Wait out any update-in-progress, then read; repeat until two reads
     * agree (avoids catching a mid-update value). */
    uint8_t statusB = cmos(0x0B);
    int bcd = !(statusB & 0x04);   /* bit2 set => binary, clear => BCD */

    uint8_t s, mi, h, d, mo, y, cent, ps = 0xff;
    for (int i = 0; i < 100; i++) {
        while (cmos(0x0A) & 0x80) { }          /* UIP */
        s = cmos(0x00); mi = cmos(0x02); h = cmos(0x04);
        d = cmos(0x07); mo = cmos(0x08); y = cmos(0x09); cent = cmos(0x32);
        if (s == ps) break;                    /* stable */
        ps = s;
    }

    if (bcd) {
        #define B(x) ((x & 0x0F) + ((x >> 4) * 10))
        s = B(s); mi = B(mi); h = B(h); d = B(d); mo = B(mo); y = B(y);
        cent = B(cent);
        #undef B
    }
    int year = (cent >= 19 && cent <= 21) ? cent * 100 + y : 2000 + y;
    return ((((uint64_t)year*100 + mo)*100 + d)*100 + h)*100*100
           + (uint64_t)mi*100 + s;
}
