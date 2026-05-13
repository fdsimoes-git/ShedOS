#!/bin/sh
# Launched by busybox getty on tty1 and ttyS0 (see /etc/inittab on the
# installer ISO). getty has already set up the tty correctly; we just
# need to invoke installer.sh.

exec /opt/shedos-installer/installer.sh
