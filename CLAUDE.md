# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

ShedOS is an Alpine-Linux–based custom OS for VMware Fusion arm64 (Apple Silicon) where Claude Opus is the **only** user-facing process. The system installs to a persistent disk via a one-shot ISO, then on every boot the user interacts with Claude through:

- **`tty1` (Fusion window)** — Chromium in app/kiosk mode showing a SPA chat GUI with tabs, markdown rendering, and "render tabs" for images / PDFs / web pages
- **`ttyS0` (Unix-socket pipe `/tmp/shedos.serial`)** — `shedos-chat.py` minimal chat client (v0.7.0+, replaces the Textual TUI), reachable via `make tui` from the host
- **SSH** — same chat client (`/opt/shedos/shedos-chat.py`), reachable via `make ssh` followed by running it from the shell

All three frontends talk to a single multi-session brain daemon over `/run/shedos-brain.sock` (JSON-RPC, one Session per chat tab, append-only JSONL persistence).

Authentication uses the Claude Code OAuth flow (Bearer + `anthropic-beta: oauth-2025-04-20`). The system prompt **must** start with `"You are Claude Code, Anthropic's official CLI for Claude."` — without that prefix, Opus rejects with a misleading "credit balance" 4xx. See `overlay/opt/shedos/anthropic_client.py`.

## Architecture (current — main, v0.5.0+)

```
host (Mac, VMware Fusion)
   ├─ vmware/shedos-system.vmdk (16 GB, persistent)
   └─ /tmp/shedos.serial (Unix socket pipe to guest's ttyS0)

guest (VM)
   tty1  ──→ run-gui.sh ──→ startx → openbox → chromium --kiosk --app=http://127.0.0.1:8080/
   ttyS0 ──→ run-chat.sh ──→ shedos-chat.py (rich-only stdin/stdout)
   sshd  ──→ root shell; run `/opt/shedos/shedos-chat.py` to chat,
            or `make tui` from the Mac (over the serial pipe)

   shedos-brain  (daemon)        ─── /run/shedos-brain.sock ─── all clients
   shedos-web    (aiohttp)       ─── 127.0.0.1:8080  HTTP + WS bridge for the GUI

   Persistence under /var/lib/shedos/
     ├─ sessions/<uuid>.jsonl     append-only chat history (1 file per chat tab)
     ├─ sessions/<uuid>.updated   sidecar timestamp (avoids rewriting meta line)
     ├─ render/<asset>/<file>     staged images/PDFs/HTML for render_* tools
     └─ render-tabs.json          manifest of open render tabs (v0.6.0)
                                  — GUI re-populates the tab bar from this
                                  on page refresh; closing a tab DELETEs
                                  the entry + wipes the asset dir.
```

The brain re-reads `/etc/shedos/persona.txt`, `/etc/shedos/persona-choice`, and `/etc/shedos/style.json` on **every turn**, so the in-GUI Settings panel can flip persona/style without restarting the daemon.

Render tools (`render_image` / `render_pdf` / `render_web` / `render_markdown` / `render_code` / `render_json` in `tools.py`) all return a `{render: {id, type, url, title}}` envelope that the GUI uses to open a tab. The markdown/code/json variants render to a self-contained HTML page under `/var/lib/shedos/render/<id>/index.html` (sandboxed iframe, no scripts) using `py3-markdown` and `py3-pygments`.

Asset IDs are `sha1(seed)[:12]`. They're **content-addressed** — same seed → same id → manifest dedupes + asset dir overwrites. Consequence: `render_web` on a brain-supplied URL **persists** the URL in `render-tabs.json` and replays it on every page refresh until the user closes that tab. That's a single-user-trusted property, but worth keeping in mind if the threat model ever loosens.

Note for upgrades from pre-v0.6.0 installs: the asset-id format changed (used to mix `time.time()` into the seed), so pre-v0.6.0 directories under `/var/lib/shedos/render/` are orphaned and will never be re-referenced. `rm -rf /var/lib/shedos/render/*` after upgrading is safe — any open render tabs will just get recreated on the next render_* call.

Upgrades from pre-v0.7.0 also leave two unused files behind: `/var/lib/shedos/theme` (was the Textual TUI's persisted theme name) and `/var/lib/shedos/prompt-history.txt` (was prompt_toolkit's input history). The new `shedos-chat.py` reads neither — safe to `rm`.

## Common commands

```bash
# Token in env is required for the brain to authenticate. Get one with `claude setup-token`.
export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...'

make iso          # builds out/shedos-installer.iso (+ creates 16 GB system VMDK if missing)
make run          # build + boot the VM in Fusion (auto-installs on first boot via wizard)
make tui          # connect to the chat client over the serial socket (needs `brew install socat`)
make console      # raw `nc -U` serial pipe — debug-only; ttyS0 hosts the chat client which needs a PTY
make ssh          # ssh root@<vm-ip> using ~/.ssh/id_ed25519
make ip           # vmrun getGuestIPAddress
make wipe-system  # delete the 16 GB system VMDK; next boot reinstalls
make distclean    # reset everything
```

## Iterating on guest code without rebuilding the ISO

Once the VM is installed and SSH is up:

```bash
scp overlay/opt/shedos/brain.py root@<vm-ip>:/opt/shedos/brain.py
ssh root@<vm-ip> 'rc-service shedos-brain restart'

# For web/JS changes (no service restart needed — reload the page):
scp overlay/opt/shedos/web/* root@<vm-ip>:/opt/shedos/web/

# For web_server.py:
scp overlay/opt/shedos/web_server.py root@<vm-ip>:/opt/shedos/
ssh root@<vm-ip> 'rc-service shedos-web restart'
```

## Build pipeline shape

`build.sh` does the actual work; `make iso` just invokes it. Key non-obvious steps:

- Downloads `alpine-virt-3.23.0-aarch64.iso` and **patches its grub.cfg** to add `apkovl=sr0:iso9660:/<name>.apkovl.tar.gz` (use `sr0`, NOT `cdrom` — the latter is a userspace symlink that doesn't exist in initramfs)
- Rewrites `console=ttyAMA0` to `console=ttyS0,115200` because Fusion emulates an 8250-style UART, not ARM PL011
- Tar uses `--uid 0 --gid 0 --uname root --gname root` so apkovl files end up owned by root in the guest (otherwise sshd's StrictModes rejects /root/.ssh/authorized_keys)
- Writes `rootfstype=ext4 rootwait` into `GRUB_CMDLINE_LINUX_DEFAULT` — busybox `mount` can't autodetect a filesystem from `UUID=...`, so without `rootfstype=ext4` the initramfs panics with a misleading "No such file or directory" trying to mount root
- Pre-creates `vmware/shedos-system.vmdk` (16 GB growable) via `/Applications/VMware Fusion.app/Contents/Library/vmware-vdiskmanager` if missing
- Bakes `$CLAUDE_CODE_OAUTH_TOKEN` (preflighted with a real `/v1/messages` call) and `~/.ssh/id_*.pub` into the installer apkovl

## Installer wizard (v0.5.0+)

On first boot the live ISO runs the wizard on **tty1** (the visible Fusion window) — that's what a fresh user sees. `ttyS0` stays as a plain `getty` so it's available as a debug shell via `make console` from the Mac (handy for tailing the install log if something goes wrong).

```
run-installer.sh ──→ apk add python3 py3-rich py3-pip open-vm-tools
                  ──→ rc-service vmtoolsd start   # so Fusion clipboard works
                  ──→ pip install textual         # not packaged for Alpine 3.23
                  ──exec──→ /usr/bin/python3 /opt/shedos-installer/wizard.py
                                       │
                                       ├─ Textual full-screen UI (rich-fallback if pip failed)
                                       ├─ welcome → token (masked) → persona → style → confirm
                                       └─ writes /tmp/shedos-wizard.env
                                                  │
                                                  └─exec──→ installer.sh
                                                              (parses the env in apply_overlay)
```

`installer.sh` keeps doing the actual disk install (parted → mkfs → `apk --root` → chroot → grub-install → reboot) and holds `/run/shedos-installer.lock` for the duration so concurrent spawns can't race on `/dev/sda`. The wizard is a thin frontend; if it crashes or is skipped, `installer.sh` falls back to baked-in defaults (default persona, terse style, ISO-baked token if any).

`open-vm-tools` is installed on both the live ISO and the target system so Fusion's host→guest clipboard channel works (paste your OAuth token into the wizard instead of typing 100+ chars). `vmtoolsd` is registered to start at boot via OpenRC's `default` runlevel.

## VMware Fusion arm64 quirks (all encoded in `vmware/shedos.vmx.tmpl`)

These are NOT auto-allocated on a minimal handcrafted .vmx. Don't remove them:

- `pciBridge0..7.present = "TRUE"` + `pciBridge4..7.virtualDev = "pcieRootPort"` + `ethernet0.pciSlotNumber = "160"` — without these you get `No PCIe slot available for Ethernet0`
- `usb.present`, `ehci.present`, `usb_xhci.present` — without these Fusion presents no USB bus at all → no virtual keyboard/mouse → keystrokes typed into the Fusion window go nowhere even though the framebuffer renders fine
- `serial0.fileType = "pipe"` + `serial0.fileName = "/tmp/shedos.serial"` — the path **must** be on APFS, NOT this project directory. `/Volumes/Untitled` is exFAT and can't host AF_UNIX sockets; bind() fails with `Operation not supported`
- `monitor.allowLegacyCPU = "TRUE"` + `firmware = "efi"` + `guestOS = "arm-other-64"` — required for arm64 guests on Apple Silicon
- X11 needs **eudev** (not busybox `mdev`); `mdev` doesn't enumerate the `/dev/input/event*` devices Xorg + libinput look for. The two device managers conflict if both are in `sysinit`, so ONLY register `udev/udev-trigger/udev-settle` and skip mdev. Without this Chromium opens but mouse + keyboard input fall on the floor.

## Repo layout

```
shedos/
├── build.sh              build pipeline; produces out/shedos-installer.iso
├── Makefile              wrappers around build.sh + vmrun
├── config/               pinned alpine version (3.23.0), arch (aarch64), target-packages.list
├── overlay/              what gets installed on the target system
│   ├── etc/
│   │   ├── inittab                  tty1 → run-gui.sh, ttyS0 → run-chat.sh
│   │   ├── init.d/{shedos-brain,shedos-web}   OpenRC services
│   │   ├── shedos/personas/         persona presets (default, coding, sysadmin, researcher)
│   │   └── shedos/style.json        conversation style flags
│   ├── opt/shedos/
│   │   ├── brain.py                 turn() loop, daemon entry point
│   │   ├── rpc_server.py            JSON-RPC server (sessions.* methods)
│   │   ├── sessions.py              SessionManager + Session (JSONL persistence)
│   │   ├── web_server.py            aiohttp HTTP + WS bridge to brain (port 8080)
│   │   ├── tools.py                 bash, apk, read/write_file, render_*
│   │   ├── anthropic_client.py      OAuth httpx client (Bearer + beta header)
│   │   ├── config.py                token/persona/style loaders + composers
│   │   ├── brain_client.py          sync RPC client (used by shedos-chat.py)
│   │   ├── shedos-chat.py           v0.7.0 minimal stdin/stdout chat client
│   │   ├── web/                     SPA frontend (index.html + app.js + style.css)
│   │   ├── run-gui.sh               startx + openbox + chromium loop
│   │   └── run-chat.sh              chat-client launcher (deps wait + token bootstrap)
│   └── root/.xinitrc                openbox + chromium respawn loop
├── installer/            apkovl baked into the live installer ISO
│   ├── etc/inittab                  tty1 → wizard.py, ttyS0 → debug getty
│   ├── etc/apk/world                python3 + py3-rich + parted + apk-tools
│   └── opt/shedos-installer/
│       ├── wizard.py                interactive preferences UI (rich + getpass)
│       ├── installer.sh             actual disk install (parted/mkfs/apk/chroot/grub)
│       └── run-installer.sh         exec wizard.py
├── vmware/               .vmx template + launch.sh + (gitignored) Fusion runtime files
└── out/                  (gitignored) shedos-installer.iso
```

## When the install gets stuck

If the post-install boot drops to an initramfs `~ #` rescue shell, you can interact via `nc -U /tmp/shedos.serial`. Most likely causes (all fixed in current main, but worth knowing):

1. `mount: mounting /dev/sda2 on /sysroot failed` — `rootfstype=ext4` missing from kernel cmdline (busybox mount can't autodetect from `UUID=`)
2. Initramfs gives up before SATA detects — `rootwait` missing
3. `/etc/fstab` has a malformed `UUID=...` line — busybox `blkid -s UUID -o value` silently dropped the flags; parse with sed instead

From the rescue shell: `mount -t ext4 /dev/sda2 /sysroot`, edit `/sysroot/boot/grub/grub.cfg` and `/sysroot/etc/default/grub` to add `rootfstype=ext4 rootwait` to the kernel cmdline, fix `/sysroot/etc/fstab`, then `echo b > /proc/sysrq-trigger` to reboot.

## Branch protection & merging

`main` is protected: PRs require @fdsimoes-git's review, but admin/owner bypass is enabled. To merge a PR using bypass:

```bash
gh pr merge <N> --merge --admin --delete-branch
```

To trigger a fresh Copilot review pass on a PR, post `@copilot review` as a regular issue comment (the REST `requested_reviewers` endpoint and the GraphQL `requestReviews` mutation both reject Copilot because it isn't a regular collaborator).

## Threat model

ShedOS is a **single-user appliance**. The `bash` tool runs as root with no sandbox. Anyone with the OAuth token has full root in the VM, and so does anyone who can prompt-inject the agent. **Don't** hand out the ISO or the system VMDK — both have the build host's OAuth token and SSH public key baked in. Don't boot it on a hostile network. Don't mount sensitive host volumes.
