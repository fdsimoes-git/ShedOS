#!/bin/sh
# Launched by busybox getty on ttyS0 (see /etc/inittab) and exposed to
# SSH users as `shedos-chat` via the /usr/local/bin/shedos-chat symlink
# (see overlay/usr/local/bin/shedos-chat -> run-chat.sh).
#
# Sequence:
#   1. wait for python3 + py3-rich (apk may still be running on first boot)
#   2. prompt for OAuth token if /etc/shedos/token is missing
#   3. wait for the brain daemon's Unix socket
#   4. exec shedos-chat.py
#
# Stays alive until python3 exits; getty respawns on exit.

while [ ! -x /usr/bin/python3 ]; do
    printf '\r[shedos] waiting for python3 to install...'
    sleep 2
done

# rich is in /etc/apk/world so should always be present; httpx ditto.
# A missing import here means something's wrong with the install.
if ! /usr/bin/python3 -c 'import httpx, rich' 2>/dev/null; then
    printf '\n[shedos] python deps (httpx / rich) missing; sleeping 5 min before getty respawn.\n'
    sleep 300
    exit 1
fi

# First-boot token bootstrap (only triggers when the wizard skipped the
# token AND the user didn't write /etc/shedos/token themselves).
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

cd /opt/shedos
exec /usr/bin/python3 /opt/shedos/shedos-chat.py
