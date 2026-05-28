#pragma once
#include <stdint.h>

void uart_init(void);
void uart_putchar(char c);
char uart_getchar(void);   /* blocking */
int  uart_trygetchar(void); /* returns -1 if no char ready */
void uart_puts(const char *s);
void uart_write(const char *buf, uint32_t len);
