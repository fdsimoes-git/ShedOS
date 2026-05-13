#!/bin/sh
# ShedOS installer — runs on first boot from the ISO. Lays Alpine + ShedOS
# down on /dev/sda as a persistent install, then reboots.
#
# Idempotency: if /dev/sda already has a labeled ShedOS root partition with
# /etc/alpine-release populated, the installer assumes we're already
# installed and just reboots (UEFI should boot from disk in that case).
#
# Re-entry guard: if getty respawns the script after a crash, the lock
# prevents a second parallel install from racing on the disk. The lock
# is held for the lifetime of the install; on success we reboot, on
# error we sleep so getty doesn't immediately respawn into a loop.

set -e

# Bail if another instance is already running (e.g., racey getty respawn).
LOCKFILE=/run/shedos-installer.lock
if [ -e "$LOCKFILE" ]; then
    OWNER_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$OWNER_PID" ] && kill -0 "$OWNER_PID" 2>/dev/null; then
        printf '\n[shedos-install] another installer (pid %s) is already running. Sleeping.\n' "$OWNER_PID"
        # Sleep forever so getty doesn't respawn into a tight loop
        sleep 86400
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"
trap 'rc=$?; echo; echo "[shedos-install] script exiting (rc=$rc) — DO NOT respawn (getty wait)"; if [ $rc -ne 0 ]; then echo "[shedos-install] FAILURE. Sleeping forever (Ctrl-Alt-F2 to a rescue tty if you want to debug)."; while :; do sleep 3600; done; fi; rm -f "$LOCKFILE"' EXIT

DISK=/dev/sda
ESP=/dev/sda1
ROOT=/dev/sda2
HOME_PART=/dev/sda3
MNT=/mnt

OVERLAY_TARBALL=/opt/shedos-installer/overlay.tar.gz
PACKAGES_FILE=/opt/shedos-installer/packages.list
ALPINE_VERSION_FILE=/etc/alpine-release
ALPINE_REPO_BASE="http://dl-cdn.alpinelinux.org/alpine"
ALPINE_VERSION="3.23"
ARCH="aarch64"

say() { printf '\n\033[1;34m[shedos-install]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[shedos-install:error]\033[0m %s\n' "$*"; exit 1; }

banner() {
    cat <<'BANNER'

╔══════════════════════════════════════════════════════════════╗
║  ShedOS installer                                            ║
║  About to install Alpine 3.23 + ShedOS to /dev/sda           ║
║  Partition scheme:                                           ║
║    /dev/sda1   256 MiB   FAT32   /boot/efi                   ║
║    /dev/sda2    4 GiB    ext4    /                           ║
║    /dev/sda3    rest     ext4    /home                       ║
║                                                              ║
║  ALL DATA ON /dev/sda WILL BE ERASED.                        ║
╚══════════════════════════════════════════════════════════════╝

BANNER
}

already_installed() {
    # Best-effort: probe /dev/sda2 for ext4 with alpine-release inside.
    blkid "$ROOT" 2>/dev/null | grep -q 'TYPE="ext4"' || return 1
    mount -t ext4 "$ROOT" "$MNT" 2>/dev/null || return 1
    if [ -f "$MNT$ALPINE_VERSION_FILE" ] && [ -d "$MNT/opt/shedos" ]; then
        umount "$MNT"
        return 0
    fi
    umount "$MNT" 2>/dev/null || true
    return 1
}

ensure_tools() {
    say "loading kernel modules"
    modprobe ext4 2>/dev/null || true
    modprobe vfat 2>/dev/null || true
    modprobe nls_iso8859-1 2>/dev/null || true
    modprobe nls_cp437 2>/dev/null || true

    say "installing installer-side tools (parted, e2fsprogs, dosfstools, apk-tools)"
    apk update >/dev/null 2>&1 || true
    apk add --no-progress parted e2fsprogs dosfstools apk-tools >/dev/null
}

wait_for_network() {
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        if nslookup dl-cdn.alpinelinux.org >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    die "no network — DHCP didn't bring up eth0 / DNS unresolvable"
}

partition_disk() {
    # If a previous (failed) install left /dev/sda partitions mounted (e.g.,
    # nlplug-findfs scanning), drop them before partitioning.
    say "ensuring $DISK is not in use"
    for p in /dev/sda1 /dev/sda2 /dev/sda3 /dev/sda4; do
        if mount | grep -q "^$p "; then
            say "  unmounting $p (was busy)"
            umount -f "$p" 2>/dev/null || umount -l "$p" 2>/dev/null || true
        fi
    done
    sync; sleep 1

    say "partitioning $DISK (GPT, ESP+root+home)"
    parted -s "$DISK" mklabel gpt \
        mkpart ESP fat32 1MiB 257MiB \
        set 1 esp on \
        mkpart shedos-root ext4 257MiB 4353MiB \
        mkpart shedos-home ext4 4353MiB 100%

    # Re-read partition table; partprobe is in parted package
    sync
    partprobe "$DISK" 2>/dev/null || true
    sleep 1
    # Some kernels need a kick — try mdev / udev triggers
    [ -x /sbin/mdev ] && /sbin/mdev -s 2>/dev/null || true
    for i in $(seq 1 30); do
        [ -b "$ESP" ] && [ -b "$ROOT" ] && [ -b "$HOME_PART" ] && return 0
        sleep 1
    done
    die "kernel didn't pick up new partition table after 30s"
}

format_disk() {
    say "formatting partitions"
    mkfs.vfat -F32 -n SHEDOS-ESP "$ESP" >/dev/null
    mkfs.ext4 -q -F -L shedos-root "$ROOT"
    mkfs.ext4 -q -F -L shedos-home "$HOME_PART"
}

mount_target() {
    say "mounting target filesystems"
    mount -t ext4 "$ROOT" "$MNT"
    mkdir -p "$MNT/boot/efi" "$MNT/home"
    mount -t vfat "$ESP" "$MNT/boot/efi"
    mount -t ext4 "$HOME_PART" "$MNT/home"
}

install_base() {
    say "installing Alpine + ShedOS packages into $MNT (this is the slow step)"
    # Configure repositories for apk --root
    mkdir -p "$MNT/etc/apk"
    cat > "$MNT/etc/apk/repositories" <<EOF
$ALPINE_REPO_BASE/v$ALPINE_VERSION/main
$ALPINE_REPO_BASE/v$ALPINE_VERSION/community
EOF
    # Bring the apk keys over from the running installer
    mkdir -p "$MNT/etc/apk/keys"
    [ -d /etc/apk/keys ] && cp -a /etc/apk/keys/. "$MNT/etc/apk/keys/" \
        || die "no apk keys on installer — can't authenticate target packages"

    # Initial DB + base
    PKGS=$(grep -vE '^#|^$' "$PACKAGES_FILE" | tr '\n' ' ')
    apk --root "$MNT" --initdb add $PKGS
}

apply_overlay() {
    say "applying ShedOS overlay to $MNT"
    [ -f "$OVERLAY_TARBALL" ] || die "overlay tarball not found at $OVERLAY_TARBALL"
    tar -xzf "$OVERLAY_TARBALL" -C "$MNT" 2>&1 || die "tar -xzf overlay failed"
    say "overlay applied. files written:"
    find "$MNT/opt/shedos" "$MNT/etc/shedos" "$MNT/etc/init.d/shedos-brain" 2>&1 | head -20 || true

    # Token + ssh key were baked into the installer apkovl by build.sh.
    if [ -f /etc/shedos/token ]; then
        say "copying token from installer -> target"
        /usr/bin/install -d -m 0700 "$MNT/etc/shedos"
        /usr/bin/install -m 0600 /etc/shedos/token "$MNT/etc/shedos/token"
    else
        say "no token on installer (first-boot will prompt)"
    fi
    if [ -f /root/.ssh/authorized_keys ]; then
        say "copying ssh authorized_keys -> target"
        /usr/bin/install -d -m 0700 "$MNT/root/.ssh"
        /usr/bin/install -m 0600 /root/.ssh/authorized_keys "$MNT/root/.ssh/authorized_keys"
    fi
    say "apply_overlay done"
}

_blkid_uuid() {
    # busybox blkid ignores -s/-o flags and dumps the full line. Parse it
    # ourselves with sed for reliability across alpine versions.
    /sbin/blkid "$1" 2>/dev/null | sed -n 's/.*UUID="\([^"]*\)".*/\1/p'
}

write_fstab() {
    say "writing /etc/fstab"
    esp_uuid=$(_blkid_uuid "$ESP")
    root_uuid=$(_blkid_uuid "$ROOT")
    home_uuid=$(_blkid_uuid "$HOME_PART")
    [ -n "$esp_uuid" ] && [ -n "$root_uuid" ] && [ -n "$home_uuid" ] \
        || die "failed to read partition UUIDs (esp=$esp_uuid root=$root_uuid home=$home_uuid)"
    say "  esp=$esp_uuid  root=$root_uuid  home=$home_uuid"
    cat > "$MNT/etc/fstab" <<EOF
UUID=$root_uuid  /          ext4  rw,relatime  0 1
UUID=$esp_uuid   /boot/efi  vfat  rw,umask=0077,nofail  0 2
UUID=$home_uuid  /home      ext4  rw,relatime  0 2
tmpfs            /tmp       tmpfs nosuid,nodev  0 0
proc             /proc      proc  defaults     0 0
sysfs            /sys       sysfs defaults     0 0
EOF
    say "fstab written"
}

chroot_setup() {
    say "chroot setup: services, initramfs, bootloader"

    # Bind-mount kernel filesystems for the chroot
    mount -t proc proc "$MNT/proc"
    mount -t sysfs sysfs "$MNT/sys"
    mount --bind /dev "$MNT/dev"
    mount -t devpts devpts "$MNT/dev/pts" 2>/dev/null || true

    chroot "$MNT" /bin/sh <<'CHROOT' 2>&1
set -e
say() { printf '\n\033[1;34m[shedos-install:chroot]\033[0m %s\n' "$*"; }

say "enabling OpenRC services"
for svc in devfs dmesg mdev hwdrivers; do rc-update add $svc sysinit; done
for svc in hwclock modules sysctl hostname bootmisc syslog networking; do rc-update add $svc boot; done
for svc in local sshd; do rc-update add $svc default; done
for svc in mount-ro killprocs savecache; do rc-update add $svc shutdown; done

say "generating SSH host keys"
ssh-keygen -A

say "generating initramfs"
KVER=$(ls /lib/modules/ | head -1)
say "  kernel: $KVER"
mkinitfs "$KVER"

say "writing /etc/default/grub"
# rootfstype=ext4 is REQUIRED — busybox's mount in the initramfs can't
# auto-detect a filesystem when root= is a UUID, so it fails with a
# misleading "No such file or directory" error without this hint.
# rootwait gives the SATA layer time to finish probing before mount.
cat > /etc/default/grub <<'GRUB'
GRUB_DISTRIBUTOR="ShedOS"
GRUB_TIMEOUT=1
GRUB_CMDLINE_LINUX_DEFAULT="rootfstype=ext4 rootwait console=tty0 console=ttyS0,115200 quiet"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"
GRUB_DISABLE_OS_PROBER=true
GRUB

say "grub-install --target=arm64-efi --efi-directory=/boot/efi --removable --no-nvram"
grub-install --target=arm64-efi --efi-directory=/boot/efi \
             --removable --no-nvram --verbose 2>&1 | tail -20

say "verifying BOOTAA64.EFI is in place"
ls -la /boot/efi/EFI/BOOT/ 2>&1 || say "  /boot/efi/EFI/BOOT missing!"

say "grub-mkconfig"
grub-mkconfig -o /boot/grub/grub.cfg

say "locking root password (key-only ssh)"
passwd -l root || true

say "cleaning apk cache"
apk cache clean >/dev/null 2>&1 || true

say "chroot section done"
CHROOT
    say "chroot_setup returned (rc=$?)"
}

unmount_target() {
    say "unmounting"
    umount "$MNT/dev/pts" 2>/dev/null || true
    umount "$MNT/dev" 2>/dev/null || true
    umount "$MNT/sys"
    umount "$MNT/proc"
    umount "$MNT/home"
    umount "$MNT/boot/efi"
    umount "$MNT"
    sync
}

do_install() {
    banner
    ensure_tools
    wait_for_network
    partition_disk
    format_disk
    mount_target
    install_base
    apply_overlay
    write_fstab
    chroot_setup
    unmount_target

    say "==================================================================="
    say "INSTALL COMPLETE. Rebooting in 5s (VM should boot from /dev/sda)..."
    say "==================================================================="
    sleep 5
    sync
    /sbin/reboot
    # If reboot didn't trip, force it the kernel way
    sleep 5
    echo b > /proc/sysrq-trigger 2>/dev/null || true
    # Final fallback: hang so getty doesn't respawn into another install
    say "reboot didn't take effect — hanging to prevent respawn loop"
    while :; do sleep 3600; done
}

# --- Entry point -------------------------------------------------------------

if already_installed; then
    say "found existing ShedOS install on $ROOT — booting that instead"
    say "if you wanted to REinstall, wipe /dev/sda first (parted /dev/sda mklabel gpt)"
    say "rebooting in 5s..."
    sleep 5
    reboot
    while :; do sleep 60; done
fi

do_install
