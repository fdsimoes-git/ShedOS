#!/bin/sh
# Tty1 banner during install. The actual installer runs on ttyS0 (so we
# don't race two installs on the same disk). User connects from their Mac
# with `make console` to watch progress.

clear 2>/dev/null
cat <<'BANNER'

  ╔══════════════════════════════════════════════════════════════╗
  ║                                                              ║
  ║    ShedOS installer is running                               ║
  ║                                                              ║
  ║    Connect from your Mac to watch progress:                  ║
  ║                                                              ║
  ║       make console                                           ║
  ║                                                              ║
  ║    When the install finishes, the VM will reboot and this    ║
  ║    window will host the brain (you'll be able to type here). ║
  ║                                                              ║
  ╚══════════════════════════════════════════════════════════════╝

BANNER

# Display the live progress underneath, refreshing every 3s
while :; do
    sleep 3
    printf '\r  ... %s\r' "$(date +%H:%M:%S) — installing"
done
