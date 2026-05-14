#!/bin/sh
# Launched by busybox getty on ttyS0 only (see /etc/inittab on the
# installer ISO). getty has already set up the tty correctly; we just
# need to invoke installer.sh. tty1 runs tty1-banner.sh instead so two
# instances don't race on /dev/sda.

exec /opt/shedos-installer/installer.sh
