#!/bin/sh
# Invoked by busybox getty on tty1/ttyS0 (see /etc/inittab). stdin/stdout/
# stderr are already attached to the tty with proper session and
# controlling-terminal semantics — no fd surgery needed.

exec /usr/bin/python3 /opt/shedos/brain.py
