#pragma once
#include "../drivers/pci.h"

/* Entry point for the Claude serial chat loop.
 * nic may be NULL if no virtio-net was found (will print an error and hang). */
void chat_run(pci_dev_t *nic);
