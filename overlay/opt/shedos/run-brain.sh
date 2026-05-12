#!/bin/sh
# Invoked by busybox getty on tty1 (see /etc/inittab). stdin/stdout/stderr
# are already attached to /dev/tty1 with proper session and controlling-
# terminal semantics — no further fd surgery needed.
#
# On a fresh boot, python3 may not be installed yet (local.d/shedos-
# packages.start installs it via apk add). Wait politely until it is.

while [ ! -x /usr/bin/python3 ]; do
    printf '\rShedOS — waiting for python3 to install...'
    sleep 2
done

# Same for httpx — fail fast with a clear message if the package install broke.
if ! /usr/bin/python3 -c 'import httpx' 2>/dev/null; then
    printf '\n[shedos] py3-httpx not importable. Check apk install.\n'
    sleep 5
    exit 1
fi

exec /usr/bin/python3 /opt/shedos/brain.py
