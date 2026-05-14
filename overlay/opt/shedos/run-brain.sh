#!/bin/sh
# Invoked by busybox getty on tty1/ttyS0 (see /etc/inittab). stdin/stdout/
# stderr are already attached to the tty with proper session and
# controlling-terminal semantics — no fd surgery needed.
#
# Sanity-check the runtime before execing python3. If python3 is missing
# or py3-httpx isn't importable, we'd otherwise crash and getty would
# tight-loop respawn us. Instead, print a clear message and sleep so the
# operator can ssh in / use tty2 to debug without console spam.

if [ ! -x /usr/bin/python3 ]; then
    printf '\n[shedos-brain] /usr/bin/python3 missing. Sleeping 5 min before respawn.\n'
    sleep 300
    exit 1
fi
if ! /usr/bin/python3 -c 'import httpx' 2>/dev/null; then
    printf '\n[shedos-brain] py3-httpx not importable. Sleeping 5 min before respawn.\n'
    sleep 300
    exit 1
fi

exec /usr/bin/python3 /opt/shedos/brain.py
