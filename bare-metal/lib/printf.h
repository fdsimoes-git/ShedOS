#pragma once
#include <stdarg.h>

/* Freestanding printf — routes output through uart_putchar(). */
int printf(const char *fmt, ...);
int vprintf(const char *fmt, va_list ap);
int snprintf(char *buf, int size, const char *fmt, ...);
int vsnprintf(char *buf, int size, const char *fmt, va_list ap);
