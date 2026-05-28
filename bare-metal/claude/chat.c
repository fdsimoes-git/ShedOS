#include "chat.h"
#include "session.h"
#include "../drivers/uart.h"
#include "../lib/printf.h"
#include <string.h>

/* TODO (Phase 2c-e): replace stubs with real net/tls/http/claude calls */

#define LINE_MAX 2048

static char linebuf[LINE_MAX];

/* Blocking readline from UART with basic editing (backspace, Ctrl-C). */
static int readline(const char *prompt, char *buf, int maxlen) {
    uart_puts(prompt);
    int pos = 0;
    while (1) {
        char c = uart_getchar();
        if (c == '\r' || c == '\n') {
            uart_puts("\n");
            buf[pos] = '\0';
            return pos;
        }
        if (c == 3) { /* Ctrl-C */
            uart_puts("^C\n");
            return -1;
        }
        if ((c == 127 || c == '\b') && pos > 0) {
            pos--;
            uart_puts("\b \b");
            continue;
        }
        if (c >= 32 && pos < maxlen - 1) {
            buf[pos++] = c;
            uart_putchar(c); /* echo */
        }
    }
}

void chat_run(pci_dev_t *nic) {
    if (!nic) {
        printf("[chat] no NIC — cannot reach api.anthropic.com\n");
        printf("[chat] halting. Check QEMU -device virtio-net-pci is present.\n");
        for (;;) __asm__("hlt");
    }

    session_init();

    printf("\n");
    printf("╔══════════════════════════════════════════════════╗\n");
    printf("║  ShedOS bare-metal  —  Claude chat (serial)     ║\n");
    printf("║  Ctrl-C to cancel a line  •  'quit' to exit     ║\n");
    printf("╚══════════════════════════════════════════════════╝\n");
    printf("\n");

    /* TODO Phase 2c: bring up virtio-net → DHCP → get IP */
    printf("[net] STUB: networking not yet implemented\n");
    printf("[net] Phase 2c (TCP/IP) will enable DHCP + DNS + HTTPS here\n\n");

    while (1) {
        int n = readline("ShedOS> ", linebuf, sizeof(linebuf));
        if (n < 0) continue; /* Ctrl-C */
        if (n == 0) continue;

        if (strcmp(linebuf, "quit") == 0 || strcmp(linebuf, "exit") == 0) {
            printf("Goodbye.\n");
            for (;;) __asm__("hlt");
        }

        session_add("user", linebuf);

        /* TODO Phase 2e: call claude_send_turn() and print real reply */
        printf("Claude: [STUB — networking not yet implemented]\n\n");
        session_add("assistant", "[stub]");
    }
}
