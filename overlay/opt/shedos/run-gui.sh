#!/bin/sh
# Launched by getty on tty1. Boots into X11 + chromium app mode.

while [ ! -x /usr/bin/startx ] || [ ! -x /usr/bin/chromium ]; do
    printf '\r[shedos-gui] waiting for X11 + chromium to install...'
    sleep 2
done

# Bootstrap token if missing (shared with TUI path).
if [ ! -f /etc/shedos/token ]; then
    /usr/bin/python3 /opt/shedos/bootstrap_token.py || {
        sleep 5
        exit 1
    }
fi

# Wait for brain daemon socket and web server.
for i in $(seq 1 60); do
    [ -S /run/shedos-brain.sock ] && break
    sleep 1
done

export HOME=/root
export USER=root
export XDG_RUNTIME_DIR=/run/user/0
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR" 2>/dev/null
cd /root
exec /usr/bin/startx /root/.xinitrc -- :0 vt1 -nolisten tcp -keeptty
