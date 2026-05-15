#!/bin/sh
# X11 startup for ShedOS GUI mode.
# Launched by /etc/inittab on tty1 via `startx`. openbox provides bare
# WM (no menus, no decorations needed because chromium is fullscreen);
# chromium runs in --kiosk --app mode against the local web GUI.

# Tell apps about the display
export DISPLAY=:0
# Better font rendering
export FONTCONFIG_PATH=/etc/fonts

# Window manager (lightweight, no menus)
openbox &
WM_PID=$!

# Hide cursor when idle (nice touch)
( command -v unclutter >/dev/null && unclutter -idle 2 -root & ) || true

# Wait for the brain web server to be reachable. shedos-web (OpenRC
# service) starts the aiohttp server on :8080 in parallel during boot.
for i in $(seq 1 60); do
    nc -z 127.0.0.1 8080 2>/dev/null && break
    sleep 1
done

# Loop Chromium so closing the window (Cmd-W / Ctrl-W) just respawns
# inside the same X session — user never sees the terminal flash from
# xinit -> getty -> startx.
while true; do
    chromium \
        --no-sandbox \
        --disable-gpu \
        --disable-dev-shm-usage \
        --kiosk \
        --app=http://127.0.0.1:8080 \
        --start-fullscreen \
        --window-position=0,0 \
        --user-data-dir=/var/lib/shedos/chromium-profile \
        --disable-features=TranslateUI,InfiniteSessionRestore \
        --disable-pinch \
        --overscroll-history-navigation=0
    sleep 1
done
