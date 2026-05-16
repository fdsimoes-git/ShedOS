#!/bin/sh
# Launched by busybox getty on tty1 (see /etc/inittab on the installer
# ISO) so the wizard is what a fresh user sees in the Fusion window.
#
# The Alpine apkovl mechanism doesn't auto-`apk add` /etc/apk/world on
# boot — packages listed there are just intent. We install everything
# the wizard needs here, then start a minimal X session that hosts the
# wizard inside fullscreen xterm.
#
# Why X for an installer? Because Fusion's host->guest clipboard channel
# (via open-vm-tools-gtk's vmware-user) is X-only, and pasting a 100+
# character OAuth token by hand is awful UX. The X session adds ~100MB
# of apk downloads on first boot but the install is one-shot.
#
# Failure handling:
#   - apk add itself fails (no network, missing package): we have no
#     python3, so we can't run the wizard at all. Skip straight to
#     installer.sh which uses baked-in defaults (default persona, terse
#     style, ISO-baked token if any). Telling the user this in the
#     console output before exec.
#   - X-only pieces fail (Xorg / openbox / xterm): apk succeeded, so
#     python3 + textual / rich exist. Fall back to running the wizard
#     directly on the framebuffer console — same UI, just without
#     host-clipboard paste.

clear 2>/dev/null
cat <<'EOF'

  ShedOS installer — preparing the environment...
  (downloading runtime + minimal X session; ~30-60s on a fresh boot)

EOF

# Wait for DHCP + DNS — the OpenRC `boot` runlevel brought networking
# up, but DHCP can take a few seconds to settle.
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    nslookup dl-cdn.alpinelinux.org >/dev/null 2>&1 && break
    sleep 1
done

apk update >/dev/null 2>&1 || true

# Step 1: install everything in /etc/apk/world. The world file lists
# python3, py3-rich, py3-pip, open-vm-tools, xorg-server, xinit, xterm,
# eudev, etc. If this fails we still try the rich-only fallback path.
echo "  installing apk packages (python3, X11, open-vm-tools, fonts)..."
if ! apk add --no-progress --quiet \
        python3 py3-rich py3-pip open-vm-tools \
        xorg-server xinit xterm openbox \
        xf86-video-fbdev xf86-input-libinput libinput-udev \
        eudev eudev-openrc open-vm-tools-gtk \
        font-jetbrains-mono font-dejavu; then
    echo
    echo "[run-installer] apk add failed — no python3 available, can't"
    echo "[run-installer] run the wizard. Falling back to non-interactive"
    echo "[run-installer] installer.sh with built-in defaults"
    echo "[run-installer]   (default persona, terse style,"
    echo "[run-installer]    ISO-baked token if any, no token override)"
    sleep 3
    exec /opt/shedos-installer/installer.sh
fi

# Step 2: bring up udev so libinput can enumerate the keyboard + mouse
# for Xorg. Without udev, Xorg loads xkb but sees no input devices and
# the wizard would render but accept no input.
echo "  starting udev + vmtoolsd..."
rc-service udev start          >/dev/null 2>&1 || true
rc-service udev-trigger start  >/dev/null 2>&1 || true
rc-service udev-settle start   >/dev/null 2>&1 || true
rc-service vmtoolsd start      >/dev/null 2>&1 || true

# Step 3: pip-install textual. Not packaged for Alpine 3.23. If this
# fails the wizard falls back to its rich-only line-by-line UI.
echo "  installing textual..."
pip install --quiet --no-cache-dir --break-system-packages textual \
    >/dev/null 2>&1 || \
    echo "[run-installer] textual install failed — will use rich fallback"

# Step 4: env for X. startx normally relies on .xserverrc + a user
# session shell to set these.
export HOME=/root USER=root
export XDG_RUNTIME_DIR=/run/user/0
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"

# Step 5: launch X. .xinitrc runs xterm fullscreen which runs wizard.py.
# If X fails to start (e.g. fbdev driver can't claim the framebuffer),
# fall through to the bare-console wizard.
if command -v startx >/dev/null && command -v xterm >/dev/null \
        && [ -f /root/.xinitrc ]; then
    chmod +x /root/.xinitrc 2>/dev/null
    startx -- -nolisten tcp vt1
    # startx returned (X crashed, xinitrc exited, or user pressed Esc on
    # the cancel screen which rebooted but reboot hasn't kicked in yet).
    # Wait briefly in case reboot is queued.
    sleep 5
fi

# Fallback path: no X, run wizard directly.
echo "[run-installer] X session unavailable — running wizard on console"
exec /usr/bin/python3 /opt/shedos-installer/wizard.py
