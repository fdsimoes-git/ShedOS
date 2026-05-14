#!/bin/sh
# Launched by busybox getty on tty1 / ttyS0 (see /etc/inittab).
# Sequence:
#   1. wait for python3 + py3-rich + py3-prompt_toolkit (apk add may still be running)
#   2. prompt for OAuth token if /etc/shedos/token is missing
#   3. wait for the brain daemon's Unix socket
#   4. exec the TUI
#
# Stays alive until python3 exits; getty respawns on exit.

while [ ! -x /usr/bin/python3 ]; do
    printf '\r[shedos] waiting for python3 to install...'
    sleep 2
done

# Ensure prompt_toolkit + rich + httpx are importable. If not, sleep so
# getty doesn't tight-loop when something's broken.
if ! /usr/bin/python3 -c 'import httpx, prompt_toolkit, rich' 2>/dev/null; then
    printf '\n[shedos] missing python deps (httpx / prompt_toolkit / rich). Sleeping 5 min before respawn.\n'
    sleep 300
    exit 1
fi

# First-boot token bootstrap
if [ ! -f /etc/shedos/token ]; then
    /usr/bin/python3 /opt/shedos/bootstrap_token.py || {
        sleep 5
        exit 1
    }
fi

# Wait for the daemon socket. The brain takes a moment to come up after
# OpenRC starts it.
for i in $(seq 1 60); do
    [ -S /run/shedos-brain.sock ] && break
    if [ "$i" = "1" ]; then
        printf '[shedos] waiting for shedos-brain daemon...\n'
    fi
    sleep 1
done

exec /usr/bin/python3 -m tui
