#!/usr/bin/env bash
# build.sh — produce a ShedOS installer ISO that wipes /dev/sda and installs
# Alpine + ShedOS to a persistent disk. On second boot the VM boots from
# /dev/sda directly.
#
# Inputs (env):
#   CLAUDE_CODE_OAUTH_TOKEN   if set, baked into the installer (and from there
#                             into /etc/shedos/token on the target)
#   SSH_PUBKEY_FILE           override path to public key
#   SKIP_TOKEN_PING           "1" to skip the model preflight check
#
# Output:
#   out/shedos-installer.iso     bootable installer
#   vmware/shedos-system.vmdk    16 GB blank disk Alpine gets installed onto

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

ALPINE_VERSION="$(tr -d '[:space:]' < config/alpine-release)"
ARCH="$(tr -d '[:space:]' < config/arch)"
ALPINE_MAJOR="${ALPINE_VERSION%.*}"
ISO_NAME="alpine-virt-${ALPINE_VERSION}-${ARCH}.iso"
ISO_URL="https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_MAJOR}/releases/${ARCH}/${ISO_NAME}"

WORK="$HERE/work"
OUT="$HERE/out"
INSTALLER_STAGE="$WORK/installer-stage"
OVERLAY_STAGE="$WORK/overlay-stage"
ISO_ORIG_DIR="$WORK/iso-orig"
ISO_RW_DIR="$WORK/iso-rw"
OUT_ISO="$OUT/shedos-installer.iso"
SYSTEM_VMDK="$HERE/vmware/shedos-system.vmdk"

log() { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[build:error]\033[0m %s\n' "$*" >&2; exit 1; }

require() {
    command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1 — install via 'brew install $2'"
}
require xorriso xorriso
require curl curl
require shasum coreutils
require python3 python

mkdir -p "$WORK" "$OUT" "$ISO_ORIG_DIR"

# --- 0. Ensure the persistent 16GB system VMDK exists -----------------------
if [[ ! -f "$SYSTEM_VMDK" ]]; then
    log "creating 16GB system disk: $SYSTEM_VMDK"
    VDM="/Applications/VMware Fusion.app/Contents/Library/vmware-vdiskmanager"
    if [[ -x "$VDM" ]]; then
        "$VDM" -c -s 16GB -a lsilogic -t 0 "$SYSTEM_VMDK" >/dev/null \
            || die "vmware-vdiskmanager failed to create $SYSTEM_VMDK"
    else
        die "vmware-vdiskmanager not found at $VDM — install VMware Fusion or set up the VMDK manually"
    fi
fi

# --- 1. Fetch + verify the Alpine ISO ---------------------------------------
ISO_PATH="$ISO_ORIG_DIR/$ISO_NAME"
SHA_URL="${ISO_URL}.sha256"
SHA_PATH="${ISO_PATH}.sha256"

if [[ ! -f "$ISO_PATH" || ! -f "$SHA_PATH" ]]; then
    log "fetching $ISO_URL"
    curl -fL -o "$ISO_PATH" "$ISO_URL"
    curl -fL -o "$SHA_PATH" "$SHA_URL"
fi
log "verifying sha256"
(cd "$ISO_ORIG_DIR" && shasum -a 256 -c "$SHA_PATH" >/dev/null) \
    || die "sha256 mismatch on $ISO_NAME"

# --- 2. Stage the TARGET overlay (gets copied to /mnt during install) -------
log "staging target overlay"
rm -rf "$OVERLAY_STAGE"
mkdir -p "$OVERLAY_STAGE"
cp -a overlay/. "$OVERLAY_STAGE/"
# Strip macOS junk
find "$OVERLAY_STAGE" -name '._*' -delete 2>/dev/null || true
find "$OVERLAY_STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$OVERLAY_STAGE" -name '*.pyc' -delete 2>/dev/null || true
# Make scripts executable
find "$OVERLAY_STAGE" -type f \
    \( -name '*.sh' -o -name '*.start' -o -name 'shedos-brain' \) \
    -exec chmod 0755 {} \;
chmod 0755 "$OVERLAY_STAGE/opt/shedos/brain.py" 2>/dev/null || true

# Pack the target overlay into a tarball for the installer to extract
TARGET_TARBALL="$WORK/overlay.tar.gz"
log "packing target overlay -> $TARGET_TARBALL"
(cd "$OVERLAY_STAGE" && tar -czf "$TARGET_TARBALL" \
    --uid 0 --gid 0 --uname root --gname root \
    etc opt)

# --- 3. Stage the INSTALLER apkovl ------------------------------------------
log "staging installer apkovl"
rm -rf "$INSTALLER_STAGE"
mkdir -p "$INSTALLER_STAGE"
cp -a installer/. "$INSTALLER_STAGE/"
find "$INSTALLER_STAGE" -name '._*' -delete 2>/dev/null || true
find "$INSTALLER_STAGE" -type f -name '*.sh' -exec chmod 0755 {} \;

# Drop the target overlay tarball + package list into the installer
mkdir -p "$INSTALLER_STAGE/opt/shedos-installer"
cp "$TARGET_TARBALL" "$INSTALLER_STAGE/opt/shedos-installer/overlay.tar.gz"
cp config/target-packages.list "$INSTALLER_STAGE/opt/shedos-installer/packages.list"

# Pin Alpine version + arch to whatever config/ says, so installer.sh
# uses the same values the build pipeline did (instead of hardcoded ones).
cat > "$INSTALLER_STAGE/opt/shedos-installer/version.env" <<EOF
ALPINE_VERSION="${ALPINE_MAJOR}"
ARCH="${ARCH}"
EOF

# Generate the installer's /etc/apk/repositories from the same pinned
# version (was a hardcoded v3.23 file before).
mkdir -p "$INSTALLER_STAGE/etc/apk"
cat > "$INSTALLER_STAGE/etc/apk/repositories" <<EOF
http://dl-cdn.alpinelinux.org/alpine/v${ALPINE_MAJOR}/main
http://dl-cdn.alpinelinux.org/alpine/v${ALPINE_MAJOR}/community
EOF

# OpenRC runlevels for the installer (minimal)
install -d -m 0755 \
    "$INSTALLER_STAGE/etc/runlevels/sysinit" \
    "$INSTALLER_STAGE/etc/runlevels/boot" \
    "$INSTALLER_STAGE/etc/runlevels/default" \
    "$INSTALLER_STAGE/etc/runlevels/shutdown"

ln -sf /etc/init.d/devfs      "$INSTALLER_STAGE/etc/runlevels/sysinit/devfs"
ln -sf /etc/init.d/dmesg      "$INSTALLER_STAGE/etc/runlevels/sysinit/dmesg"
ln -sf /etc/init.d/mdev       "$INSTALLER_STAGE/etc/runlevels/sysinit/mdev"
ln -sf /etc/init.d/hwdrivers  "$INSTALLER_STAGE/etc/runlevels/sysinit/hwdrivers"
ln -sf /etc/init.d/modloop    "$INSTALLER_STAGE/etc/runlevels/sysinit/modloop"

ln -sf /etc/init.d/hwclock    "$INSTALLER_STAGE/etc/runlevels/boot/hwclock"
ln -sf /etc/init.d/modules    "$INSTALLER_STAGE/etc/runlevels/boot/modules"
ln -sf /etc/init.d/sysctl     "$INSTALLER_STAGE/etc/runlevels/boot/sysctl"
ln -sf /etc/init.d/hostname   "$INSTALLER_STAGE/etc/runlevels/boot/hostname"
ln -sf /etc/init.d/bootmisc   "$INSTALLER_STAGE/etc/runlevels/boot/bootmisc"
ln -sf /etc/init.d/syslog     "$INSTALLER_STAGE/etc/runlevels/boot/syslog"
ln -sf /etc/init.d/networking "$INSTALLER_STAGE/etc/runlevels/boot/networking"

ln -sf /etc/init.d/mount-ro   "$INSTALLER_STAGE/etc/runlevels/shutdown/mount-ro"
ln -sf /etc/init.d/killprocs  "$INSTALLER_STAGE/etc/runlevels/shutdown/killprocs"

# --- 4. Bake OAuth token into the installer (will be carried to /mnt) -------
TOKEN_FILE="$INSTALLER_STAGE/etc/shedos/token"
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    log "baking OAuth token into installer"
    install -d -m 0700 "$INSTALLER_STAGE/etc/shedos"
    printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"

    if [[ "${SKIP_TOKEN_PING:-0}" != "1" ]]; then
        log "preflight: hitting /v1/messages once to validate model + token"
        python3 - <<'PY' || die "preflight failed — see error above"
import json, os, sys, urllib.request
tok = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
body = json.dumps({
    "model": "claude-opus-4-6",
    "max_tokens": 16,
    "system": [
        {"type":"text","text":"You are Claude Code, Anthropic's official CLI for Claude."},
        {"type":"text","text":"Reply with the word OK."},
    ],
    "messages": [{"role":"user","content":"ping"}],
}).encode()
req = urllib.request.Request(
    "https://api.anthropic.com/v1/messages",
    data=body,
    headers={
        "authorization": f"Bearer {tok}",
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    },
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
        print("[preflight] ok:", data.get("model"), data.get("stop_reason"))
except urllib.error.HTTPError as e:
    print("[preflight] HTTP", e.code, e.read().decode("utf-8","replace"), file=sys.stderr)
    sys.exit(1)
PY
    fi
else
    log "no CLAUDE_CODE_OAUTH_TOKEN set — first-boot prompt will run on tty1 after install"
    rm -f "$TOKEN_FILE"
fi

# --- 5. Inject SSH pubkey ---------------------------------------------------
PUBKEY_PATH="${SSH_PUBKEY_FILE:-}"
if [[ -z "$PUBKEY_PATH" ]]; then
    for cand in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub" "$HOME/.ssh/id_ecdsa.pub"; do
        [[ -f "$cand" ]] && PUBKEY_PATH="$cand" && break
    done
fi
if [[ -z "$PUBKEY_PATH" || ! -f "$PUBKEY_PATH" ]]; then
    die "no SSH pubkey found — generate one with 'ssh-keygen -t ed25519' or set SSH_PUBKEY_FILE"
fi
log "embedding SSH pubkey: $PUBKEY_PATH"
install -d -m 0700 "$INSTALLER_STAGE/root/.ssh"
cp "$PUBKEY_PATH" "$INSTALLER_STAGE/root/.ssh/authorized_keys"
chmod 600 "$INSTALLER_STAGE/root/.ssh/authorized_keys"

# --- 6. Build the installer apkovl tarball ----------------------------------
APKOVL="$WORK/shedos-installer.apkovl.tar.gz"
log "building $APKOVL"
(cd "$INSTALLER_STAGE" && tar -czf "$APKOVL" \
    --uid 0 --gid 0 --uname root --gname root \
    etc root opt)

# --- 7. Copy ISO contents into a writable tree ------------------------------
log "extracting Alpine ISO"
rm -rf "$ISO_RW_DIR"
mkdir -p "$ISO_RW_DIR"
xorriso -osirrox on -indev "$ISO_PATH" -extract / "$ISO_RW_DIR" >/dev/null
chmod -R u+w "$ISO_RW_DIR"
# Strip macOS metadata that piggybacks on the exFAT working volume
find "$ISO_RW_DIR" -name '._*' -delete 2>/dev/null || true

# --- 8. Drop apkovl on the ISO root + name copies ---------------------------
cp "$APKOVL" "$ISO_RW_DIR/localhost.apkovl.tar.gz"
cp "$APKOVL" "$ISO_RW_DIR/shedos-installer.apkovl.tar.gz"

# --- 9. Patch the bootloader's kernel cmdline -------------------------------
APKOVL_ARG="apkovl=sr0:iso9660:/shedos-installer.apkovl.tar.gz"
EDITED=0
for cfg in \
    "$ISO_RW_DIR/boot/grub/grub.cfg" \
    "$ISO_RW_DIR/efi/boot/grub.cfg" \
    "$ISO_RW_DIR/EFI/boot/grub.cfg" \
    "$ISO_RW_DIR/boot/syslinux/syslinux.cfg" \
    "$ISO_RW_DIR/boot/syslinux/extlinux.conf"; do
    if [[ -f "$cfg" ]]; then
        log "patching $cfg"
        python3 - "$cfg" "$APKOVL_ARG" <<'PY'
import re, sys
path, arg = sys.argv[1], sys.argv[2]
with open(path) as f:
    src = f.read()
src = re.sub(r'console=ttyAMA0(,\d+)?', 'console=ttyS0,115200', src)
def patch(m):
    return m.group(0).rstrip() + f" {arg}\n"
new = re.sub(r'(?m)^\s*linux\s+.*$\n?', patch, src)
new = re.sub(r'(?m)^\s*append\s+.*$\n?', patch, new)
with open(path, 'w') as f:
    f.write(new)
PY
        EDITED=1
    fi
done
[[ "$EDITED" = "1" ]] || die "no recognized bootloader config found in ISO"

# --- 10. Repack the ISO -----------------------------------------------------
log "repacking ISO -> $OUT_ISO"
rm -f "$OUT_ISO"

MAP_ARGS=()
while IFS= read -r f; do
    rel="${f#$ISO_RW_DIR}"
    MAP_ARGS+=( -map "$f" "$rel" )
done < <(find "$ISO_RW_DIR" -type f \
            \( -path '*/shedos-installer.apkovl.tar.gz' \
            -o -path '*/localhost.apkovl.tar.gz' \
            -o -path '*/grub*' \
            -o -path '*/syslinux.cfg' \
            -o -path '*/extlinux.conf' \))

xorriso -indev "$ISO_PATH" \
        -outdev "$OUT_ISO" \
        "${MAP_ARGS[@]}" \
        -boot_image any replay >/dev/null

# --- 11. Summary ------------------------------------------------------------
size=$(stat -f '%z' "$OUT_ISO" 2>/dev/null || stat -c '%s' "$OUT_ISO")
sha=$(shasum -a 256 "$OUT_ISO" | awk '{print $1}')
human=$(( size / 1024 / 1024 ))
log "done."
printf '\n'
printf '  ISO:    %s\n' "$OUT_ISO"
printf '  size:   %s MiB\n' "$human"
printf '  sha256: %s\n' "$sha"
printf '  system: %s (16 GB)\n' "$SYSTEM_VMDK"
printf '\n'
printf '  Next:   make run        # boot ISO -> auto-installs onto disk -> reboots into ShedOS\n'
printf '          make console    # talk to the brain over the serial pipe\n'
printf '\n'
