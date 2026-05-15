#!/bin/sh
# Launched by busybox getty on ttyS0 only (see /etc/inittab on the
# installer ISO). getty has already set up the tty correctly; we just
# need to invoke the wizard, which collects preferences and then
# exec's installer.sh. tty1 runs tty1-banner.sh instead so two
# instances don't race on /dev/sda.

exec /usr/bin/python3 /opt/shedos-installer/wizard.py
