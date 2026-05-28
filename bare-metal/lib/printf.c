#include "printf.h"
#include "../drivers/uart.h"
#include <stdint.h>
#include <stddef.h>

/* Minimal freestanding printf. Supports: %d %i %u %x %X %s %c %p %% %zu %llu */

static void emit_str(char *buf, int *pos, int size, const char *s) {
    while (*s) {
        if (buf) {
            if (*pos < size - 1) buf[(*pos)++] = *s;
        } else {
            uart_putchar(*s);
            (*pos)++;
        }
        s++;
    }
}

static void emit_char(char *buf, int *pos, int size, char c) {
    if (buf) {
        if (*pos < size - 1) buf[(*pos)++] = c;
    } else {
        uart_putchar(c);
        (*pos)++;
    }
}

static void emit_uint64(char *buf, int *pos, int size,
                        uint64_t val, int base, int upper, int width, char pad) {
    char tmp[24];
    int  len = 0;
    const char *dig = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    if (val == 0) { tmp[len++] = '0'; }
    else { while (val) { tmp[len++] = dig[val % base]; val /= base; } }
    for (int i = width - len; i > 0; i--) emit_char(buf, pos, size, pad);
    for (int i = len - 1; i >= 0; i--) emit_char(buf, pos, size, tmp[i]);
}

int vsnprintf(char *buf, int size, const char *fmt, va_list ap) {
    int pos = 0;
    while (*fmt) {
        if (*fmt != '%') { emit_char(buf, &pos, size, *fmt++); continue; }
        fmt++;
        char pad = ' ';
        int width = 0;
        if (*fmt == '0') { pad = '0'; fmt++; }
        while (*fmt >= '0' && *fmt <= '9') { width = width * 10 + (*fmt++ - '0'); }
        int lng = 0;
        while (*fmt == 'l' || *fmt == 'z') { lng++; fmt++; }

        switch (*fmt++) {
        case 'd': case 'i': {
            int64_t v = lng >= 2 ? va_arg(ap, long long) : va_arg(ap, int);
            if (v < 0) { emit_char(buf, &pos, size, '-'); v = -v; }
            emit_uint64(buf, &pos, size, (uint64_t)v, 10, 0, width, pad);
            break;
        }
        case 'u':
            emit_uint64(buf, &pos, size,
                lng >= 2 ? va_arg(ap, unsigned long long) : va_arg(ap, unsigned), 10, 0, width, pad);
            break;
        case 'x':
            emit_uint64(buf, &pos, size,
                lng >= 2 ? va_arg(ap, unsigned long long) : va_arg(ap, unsigned), 16, 0, width, pad);
            break;
        case 'X':
            emit_uint64(buf, &pos, size,
                lng >= 2 ? va_arg(ap, unsigned long long) : va_arg(ap, unsigned), 16, 1, width, pad);
            break;
        case 'p':
            emit_str(buf, &pos, size, "0x");
            emit_uint64(buf, &pos, size, (uint64_t)(uintptr_t)va_arg(ap, void*), 16, 0, 16, '0');
            break;
        case 's': {
            const char *s = va_arg(ap, const char*);
            emit_str(buf, &pos, size, s ? s : "(null)");
            break;
        }
        case 'c':
            emit_char(buf, &pos, size, (char)va_arg(ap, int));
            break;
        case '%':
            emit_char(buf, &pos, size, '%');
            break;
        default:
            emit_char(buf, &pos, size, '?');
            break;
        }
    }
    if (buf && size > 0) buf[pos < size ? pos : size - 1] = '\0';
    return pos;
}

int vprintf(const char *fmt, va_list ap) {
    return vsnprintf(NULL, 0x7FFFFFFF, fmt, ap);
}

int printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = vprintf(fmt, ap);
    va_end(ap);
    return n;
}

int snprintf(char *buf, int size, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, size, fmt, ap);
    va_end(ap);
    return n;
}
