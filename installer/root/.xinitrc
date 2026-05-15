#!/bin/sh
# ShedOS installer X session.
#
# Launched by `startx` from run-installer.sh once Xorg + open-vm-tools-gtk
# are installed. The whole purpose of this X session is to host the wizard
# inside xterm so:
#   - colours render with 24-bit terminfo instead of 16-colour fb console
#   - Fusion host -> guest clipboard works (vmware-user bridges the host
#     clipboard into X's CLIPBOARD selection, which xterm consumes)
#
# We don't run a window manager — a single fullscreen xterm covers the
# whole screen. After the wizard exec's installer.sh, the install logs
# stream into the same xterm. installer.sh issues `reboot` at the end,
# which kills X and reboots into the installed system.

# vmware-user-suid-wrapper handles the clipboard channel + drag-and-drop.
# Open-vm-tools-gtk installs it; vmtoolsd should already be running.
/usr/bin/vmware-user-suid-wrapper 2>/dev/null &

# Disable the screen blanker / DPMS — install logs may sit idle for a
# minute or two during apk add.
xset s off -dpms 2>/dev/null

# Window manager. xterm -fullscreen uses _NET_WM_STATE_FULLSCREEN, which
# requires an EWMH-compliant WM. Without one, xterm renders at default
# 80x24. openbox handles the hint correctly.
openbox &
# Give openbox a moment to register as the WM before xterm requests the
# fullscreen state — otherwise the property is set on root and ignored.
sleep 1

# Fullscreen, borderless, hide-mouse-while-typing, BIOS-blue background.
# JetBrains Mono is the same monospace face the installed GUI uses.
exec xterm \
    -fullscreen \
    -bw 0 \
    -fa "JetBrains Mono" -fs 13 \
    -bg "#00008B" -fg "#d0d0d0" \
    -title "ShedOS Setup Utility" \
    -xrm "XTerm*selectToClipboard: true" \
    -e /usr/bin/python3 /opt/shedos-installer/wizard.py
