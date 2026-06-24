#!/bin/sh
# detect-disk.sh — print the best install-target block device, e.g. /dev/nvme0n1
#
# Single source of truth for "which disk does ShedOS get installed onto".
# Used both by the wizard (to show the target on the confirm screen) and by
# installer.sh (to actually partition it), so the two can never disagree.
#
# Selection: the LARGEST fixed (non-removable) whole disk that is NOT the live
# boot medium. This works on real hardware (NVMe/SATA), in VMware (sda) and in
# QEMU (virtio vda) alike. CD-ROMs, loop/ram/zram/dm/md virtual devices, and the
# USB stick the installer itself booted from are all excluded.
#
# Prints the chosen device path on stdout and exits 0; prints nothing and exits
# 1 if no eligible disk was found (e.g. only the boot medium is attached).

# Parent disk backing the live boot medium, so we never install onto the USB
# stick / CD we were launched from. Look at where Alpine mounted the boot repo
# (/media/*) and the read-only modloop, map those back to their whole disk.
live_parent() {
    # Collect source devices of any mount under /media or the modloop.
    grep -E ' (/media/[^ ]+|/\.modloop[^ ]*) ' /proc/mounts 2>/dev/null \
        | awk '{print $1}' | while read -r src; do
        [ -b "$src" ] || continue
        base=${src##*/}                      # e.g. sdb1, nvme0n1p2, sr0
        # Strip partition suffix to get the whole-disk name.
        case "$base" in
            nvme*|mmcblk*) disk=$(echo "$base" | sed -E 's/p[0-9]+$//') ;;
            *)            disk=$(echo "$base" | sed -E 's/[0-9]+$//') ;;
        esac
        echo "/dev/$disk"
    done | sort -u
}

LIVE_PARENTS=$(live_parent)

best=""
best_sectors=0
for d in /sys/block/*; do
    name=${d##*/}
    case "$name" in
        loop*|ram*|zram*|sr*|fd*|md*|dm-*|nbd*) continue ;;
    esac
    # Skip removable media (the typical USB-installer case).
    [ "$(cat "$d/removable" 2>/dev/null)" = "1" ] && continue
    # Skip the device we booted from, even if it reports non-removable.
    dev="/dev/$name"
    skip=0
    for p in $LIVE_PARENTS; do
        [ "$dev" = "$p" ] && skip=1
    done
    [ "$skip" = "1" ] && continue
    sectors=$(cat "$d/size" 2>/dev/null || echo 0)
    [ "$sectors" -gt 0 ] 2>/dev/null || continue
    if [ "$sectors" -gt "$best_sectors" ]; then
        best_sectors=$sectors
        best="$dev"
    fi
done

[ -n "$best" ] || exit 1
echo "$best"
