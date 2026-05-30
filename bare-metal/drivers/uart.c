#include "uart.h"
#include <stdint.h>

/* 16550 UART — COM1 at I/O port 0x3F8, 115200 baud */
#define COM1 0x3F8

static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}
static inline uint8_t inb(uint16_t port) {
    uint8_t val;
    __asm__ volatile("inb %1, %0" : "=a"(val) : "Nd"(port));
    return val;
}

void uart_init(void) {
    outb(COM1 + 1, 0x00); /* disable interrupts */
    outb(COM1 + 3, 0x80); /* enable DLAB (baud rate divisor latch) */
    outb(COM1 + 0, 0x01); /* divisor low byte  = 1  → 115200 baud */
    outb(COM1 + 1, 0x00); /* divisor high byte = 0 */
    outb(COM1 + 3, 0x03); /* 8 bits, no parity, 1 stop bit (DLAB off) */
    outb(COM1 + 2, 0xC7); /* enable + clear FIFO, 14-byte threshold */
    outb(COM1 + 4, 0x0B); /* RTS/DSR set, OUT2 enabled (IRQ gate) */
}

static int tx_ready(void) { return inb(COM1 + 5) & 0x20; }
static int rx_ready(void) { return inb(COM1 + 5) & 0x01; }

void uart_putchar(char c) {
    if (c == '\n')
        uart_putchar('\r');
    while (!tx_ready());
    outb(COM1, (uint8_t)c);
}

char uart_getchar(void) {
    while (!rx_ready());
    return (char)inb(COM1);
}

int uart_trygetchar(void) {
    if (!rx_ready()) return -1;
    return (unsigned char)inb(COM1);
}

void uart_puts(const char *s) {
    while (*s) uart_putchar(*s++);
}

void uart_write(const char *buf, uint32_t len) {
    for (uint32_t i = 0; i < len; i++)
        uart_putchar(buf[i]);
}
