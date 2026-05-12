#!/usr/bin/env bash
# build.sh — assemble shedos.iso from an Alpine virt ISO + our overlay.
#
# Inputs (env):
#   CLAUDE_CODE_OAUTH_TOKEN   if set, baked into the ISO at /etc/shedos/token
#   SSH_PUBKEY_FILE           override path to public key (default: auto-detect)
#   SKIP_TOKEN_PING           "1" to skip the model preflight check
#
# Output:
#   out/shedos.iso

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
STAGE="$WORK/stage"
ISO_ORIG_DIR="$WORK/iso-orig"
ISO_RW_DIR="$WORK/iso-rw"

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

# --- 2. Stage overlay --------------------------------------------------------
log "staging overlay"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a overlay/. "$STAGE/"

# Normalize line endings (in case anyone edited on macOS).
find "$STAGE" -type f \( -name '*.sh' -o -name '*.start' -o -name 'shedos-brain' \) \
    -exec chmod 755 {} \;

# --- 3. OpenRC runlevel symlinks --------------------------------------------
log "wiring OpenRC runlevels"
install -d -m 0755 \
    "$STAGE/etc/runlevels/sysinit" \
    "$STAGE/etc/runlevels/boot" \
    "$STAGE/etc/runlevels/default" \
    "$STAGE/etc/runlevels/shutdown"

ln -sf /etc/init.d/devfs       "$STAGE/etc/runlevels/sysinit/devfs"
ln -sf /etc/init.d/dmesg       "$STAGE/etc/runlevels/sysinit/dmesg"
ln -sf /etc/init.d/mdev        "$STAGE/etc/runlevels/sysinit/mdev"
ln -sf /etc/init.d/hwdrivers   "$STAGE/etc/runlevels/sysinit/hwdrivers"
ln -sf /etc/init.d/modloop     "$STAGE/etc/runlevels/sysinit/modloop"

ln -sf /etc/init.d/hwclock     "$STAGE/etc/runlevels/boot/hwclock"
ln -sf /etc/init.d/modules     "$STAGE/etc/runlevels/boot/modules"
ln -sf /etc/init.d/sysctl      "$STAGE/etc/runlevels/boot/sysctl"
ln -sf /etc/init.d/hostname    "$STAGE/etc/runlevels/boot/hostname"
ln -sf /etc/init.d/bootmisc    "$STAGE/etc/runlevels/boot/bootmisc"
ln -sf /etc/init.d/syslog      "$STAGE/etc/runlevels/boot/syslog"
ln -sf /etc/init.d/networking  "$STAGE/etc/runlevels/boot/networking"

ln -sf /etc/init.d/local       "$STAGE/etc/runlevels/default/local"
ln -sf /etc/init.d/sshd        "$STAGE/etc/runlevels/default/sshd"
# shedos-brain is launched by getty via inittab — see overlay/etc/inittab.
# We keep the init.d file around for manual `rc-service shedos-brain restart`,
# but don't activate it in the default runlevel.

ln -sf /etc/init.d/mount-ro    "$STAGE/etc/runlevels/shutdown/mount-ro"
ln -sf /etc/init.d/killprocs   "$STAGE/etc/runlevels/shutdown/killprocs"
ln -sf /etc/init.d/savecache   "$STAGE/etc/runlevels/shutdown/savecache"

# --- 4. Inject token if env is set ------------------------------------------
TOKEN_FILE="$STAGE/etc/shedos/token"
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    log "baking OAuth token into ISO"
    install -d -m 0700 "$STAGE/etc/shedos"
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
    log "no CLAUDE_CODE_OAUTH_TOKEN set — first-boot prompt will run on tty1"
    rm -f "$TOKEN_FILE"
fi

# --- 5. Inject SSH public key -----------------------------------------------
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
install -d -m 0700 "$STAGE/root/.ssh"
cp "$PUBKEY_PATH" "$STAGE/root/.ssh/authorized_keys"
chmod 600 "$STAGE/root/.ssh/authorized_keys"

# --- 6. Build the apkovl tarball --------------------------------------------
APKOVL="$WORK/shedos.apkovl.tar.gz"
log "building $APKOVL"
# Tarball root contains etc/, root/, opt/ directly — Alpine convention.
(cd "$STAGE" && tar -czf "$APKOVL" etc root opt)

# --- 7. Copy ISO contents into a writable tree ------------------------------
log "extracting Alpine ISO"
rm -rf "$ISO_RW_DIR"
mkdir -p "$ISO_RW_DIR"
xorriso -osirrox on -indev "$ISO_PATH" -extract / "$ISO_RW_DIR" >/dev/null
# xorriso extracts with read-only perms; make writable.
chmod -R u+w "$ISO_RW_DIR"

# --- 8. Place apkovl on the ISO root ---------------------------------------
# Two copies, both at ISO root, for redundancy:
#  - localhost.apkovl.tar.gz : Alpine's nlplug-findfs auto-discovers
#    "${hostname}.apkovl.tar.gz" from any scanned block device. At boot,
#    before our overlay applies, the hostname is "localhost".
#  - shedos.apkovl.tar.gz    : referenced by the explicit apkovl= kernel arg
#    via the actual block-device name (sr0 for SATA optical).
cp "$APKOVL" "$ISO_RW_DIR/localhost.apkovl.tar.gz"
cp "$APKOVL" "$ISO_RW_DIR/shedos.apkovl.tar.gz"

# --- 9. Edit the bootloader config to add the apkovl= kernel arg ------------
# Use sr0 (the real Linux device name for the SATA CD-ROM the initramfs sees)
# — "cdrom" is a userspace symlink that doesn't exist at this stage.
APKOVL_ARG="apkovl=sr0:iso9660:/shedos.apkovl.tar.gz"
EDITED=0
for cfg in \
    "$ISO_RW_DIR/boot/grub/grub.cfg" \
    "$ISO_RW_DIR/efi/boot/grub.cfg" \
    "$ISO_RW_DIR/EFI/boot/grub.cfg" \
    "$ISO_RW_DIR/boot/syslinux/syslinux.cfg" \
    "$ISO_RW_DIR/boot/syslinux/extlinux.conf"; do
    if [[ -f "$cfg" ]]; then
        log "patching $cfg"
        # Patch the bootloader's kernel cmdline:
        #  (a) Append apkovl= so initramfs auto-applies our overlay.
        #  (b) Rewrite console=ttyAMA0 -> console=ttyS0,115200 — Fusion's
        #      emulated serial appears as ttyS0 in the guest; ttyAMA0 (ARM
        #      PL011) doesn't exist here, so kernel printk to it is dropped.
        python3 - "$cfg" "$APKOVL_ARG" <<'PY'
import re, sys
path, arg = sys.argv[1], sys.argv[2]
with open(path) as f:
    src = f.read()
# Route console output to the serial port that actually exists.
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
OUT_ISO="$OUT/shedos.iso"
log "repacking ISO -> $OUT_ISO"
rm -f "$OUT_ISO"

# Build a flat list of -map args from the writable tree so xorriso copies
# every file we changed back into the output, preserving the original
# bootloader records via -boot_image any replay.
MAP_ARGS=()
while IFS= read -r f; do
    rel="${f#$ISO_RW_DIR}"
    MAP_ARGS+=( -map "$f" "$rel" )
done < <(find "$ISO_RW_DIR" -type f \
            \( -path '*/shedos.apkovl.tar.gz' \
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
printf '\n'
printf '  Next:   make run     # opens in VMware Fusion\n'
printf '\n'
