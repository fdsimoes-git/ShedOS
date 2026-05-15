# ShedOS

A minimal Linux distro where **Claude is the only interface**. You boot the ISO
in VMware Fusion, the installer lays Alpine + ShedOS onto a persistent disk,
reboots, and from then on you talk to Claude in natural language — through
either the Fusion window (tty1) or a Mac terminal connected via `make console`
(ttyS0 over a Unix socket).

Think OpenClaw, but instead of running on top of an OS, the agent *is* the
shell of a self-contained installed system.

## How it works

```
┌─ first boot ────────────────────────────────────────────────┐
│  VM has 2 disks:                                            │
│    sata0:0 = shedos-installer.iso (live installer)          │
│    sata0:1 = shedos-system.vmdk   (16 GB, empty)            │
│                                                             │
│  UEFI finds no bootloader on disk -> falls back to the CD.  │
│  Installer runs on ttyS0 (tty1 shows a banner):             │
│    parted GPT  | mkfs vfat/ext4/ext4                        │
│    apk --root /mnt --initdb add (target-packages.list)      │
│    extract overlay/ -> /mnt                                 │
│    /etc/fstab from blkid UUIDs                              │
│    chroot: rc-update services, mkinitfs, grub-install       │
│            --target=arm64-efi --removable                   │
│    reboot                                                   │
└─────────────────────────────────────────────────────────────┘
┌─ every subsequent boot ─────────────────────────────────────┐
│  UEFI finds \EFI\BOOT\BOOTAA64.EFI on sata0:1  →  GRUB →    │
│  kernel + initramfs (on /dev/sda2) → /sbin/init → OpenRC →  │
│  inittab: getty -l run-brain.sh on tty1 + ttyS0  →  brain   │
└─────────────────────────────────────────────────────────────┘
```

Partition scheme on `/dev/sda` (16 GB total):

| Partition | Size      | FS    | Mount     |
|-----------|-----------|-------|-----------|
| sda1 ESP  | 256 MiB   | FAT32 | /boot/efi |
| sda2 root | 4 GiB     | ext4  | /         |
| sda3 home | ~11.7 GiB | ext4  | /home     |

Brain conversation history is auto-saved to `/var/lib/shedos/brain-<tty>.jsonl`
(one JSON message per line), reloaded on every boot — so reboots no longer
lose context.

OAuth uses the Claude Code flow: `Bearer` auth, `anthropic-beta: oauth-2025-04-20`,
mandatory `"You are Claude Code..."` system prompt prefix.

## Requirements (host)

- macOS on Apple Silicon (Alpine arm64 + Fusion arm64 VM)
- VMware Fusion 13+
- `xorriso`, `coreutils`, `python3`, `socat` — `brew install xorriso coreutils python@3 socat`
  (socat is required for `make tui` — puts the host terminal in raw mode so keys + colors work)
- An SSH keypair at `~/.ssh/id_ed25519` (or `id_rsa`, or `id_ecdsa`)
- A Claude Code OAuth token. Get one with `claude setup-token` on any host
  with Claude Code installed. Token starts with `sk-ant-oat01-`.

## Build & install

```bash
export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...'
make iso          # builds out/shedos-installer.iso + 16 GB vmware/shedos-system.vmdk
make run          # boots VM in Fusion; first boot auto-installs to disk (~5 min)
```

If you skip the token export, the installer still works but the brain will
prompt for the token on first interactive boot. Less convenient — the token
also doesn't get persisted to the disk via the installer.

Install takes ~5-10 min the first time (apk fetches packages from the Alpine
CDN). After it reboots into the installed system, boots are ~5-10 seconds.

## Talking to it

```bash
make tui                      # full TUI (themes, tabs, markdown, animated tools)
make console                  # raw serial pipe (nc -U) — for debugging only;
                              # ttyS0 hosts the Textual TUI which needs a PTY,
                              # so use `make tui` for the real interface
make ssh                      # ssh into the VM (best for shell access)
```

The TUI is built with [Textual](https://textual.textualize.io) (full-screen
modern Python TUI framework — installed in the guest via pip during install
since it's not packaged for Alpine 3.23). Features:

- 6 builtin themes (Ctrl-K cycles; `/theme nord|dracula|tokyo-night|gruvbox|solarized-dark|monokai`)
- Cards with rounded borders, padding, and themed backgrounds (you / claude / tool / error all visually distinct)
- Markdown widget renders Claude's responses with syntax-highlighted code blocks, tables, lists
- Tabbed conversations (`Ctrl-T` new, `Ctrl-W` close, `Ctrl-N`/`Ctrl-P` cycle)
- Per-tab scrollable chat history (auto-loaded on startup from `/var/lib/shedos/sessions/`)
- Tool calls show a yellow card with command + args, then turn green (success) or red (failure) with the output
- Header + Footer with key-binding hints
- Slash commands: `/help`, `/new`, `/close`, `/next`, `/prev`, `/theme`, `/title`, `/quit`

```
> what's my IP?
[tool: bash(command="ip -4 addr show eth0")]
<vm-ip>    # Fusion NAT subnet varies by host config; check yours with `make ip`

> install nginx and serve a one-page site that says "hello from shedos"
[tool: apk(args="add nginx")]
[tool: write_file(path="/var/www/localhost/htdocs/index.html", ...)]
[tool: bash(command="rc-service nginx start")]
Done — nginx running on :80.
```

Tools available to the agent:

| tool | purpose |
|---|---|
| `bash` | run a shell command as root |
| `apk` | Alpine package manager (alias for `bash` but explicit) |
| `read_file`, `write_file`, `list_dir` | filesystem |
| `process_list`, `process_kill` | process control |
| `net_fetch` | HTTP GET |

Two independent brain instances run in parallel — one on `tty1` (Fusion
framebuffer keyboard) and one on `ttyS0` (Mac-side `make console`). They
have **separate** conversation histories (`brain-tty1.jsonl` vs
`brain-ttyS0.jsonl`), so what you say in the window doesn't affect the
serial session and vice versa.

## SSH escape hatch

```bash
make ip            # prints the VM's NAT IP
make ssh           # ssh root@<that ip> using your key
```

Key-only login as root. Useful when the brain wedges or you want to do
something brain-free (`apk upgrade`, file recovery, etc.).

## Updating the OS

```bash
ssh root@<vm-ip>
apk upgrade        # in-place upgrade of all packages
reboot             # so the new kernel is picked up
```

For structural changes (new brain code, new persona, new services), the
fastest path is to `scp` files into `/opt/shedos/` then `pkill -f brain.py`
so getty respawns it with the updated code. For deeper changes, rebuild
the ISO and `make wipe-system && make run` to reinstall.

## Repo layout

```
shedos/
├── README.md          (this file)
├── Makefile           iso, run, console, ssh, ip, wipe-system, …
├── build.sh           build pipeline
├── config/
│   ├── alpine-release "3.23.0"
│   ├── arch           "aarch64"
│   └── target-packages.list   apk packages for the installed system
├── installer/         lives in the installer ISO's apkovl
│   ├── etc/inittab    runs installer.sh on tty1 + ttyS0
│   ├── etc/apk/world  apk packages for the live installer itself
│   └── opt/shedos-installer/installer.sh
├── overlay/           gets copied to /mnt during install (the target system)
│   ├── etc/inittab    runs brain on tty1 + ttyS0 (post-install)
│   ├── etc/init.d/shedos-brain
│   ├── etc/shedos/persona.txt
│   └── opt/shedos/    brain.py, tools.py, anthropic_client.py, …
├── vmware/
│   ├── shedos.vmx.tmpl       Fusion arm64 VM config
│   ├── shedos-system.vmdk    16 GB system disk (built once, persists)
│   └── launch.sh
└── out/               shedos-installer.iso (gitignored)
```

## Threat model — read this

ShedOS is a **single-user appliance**. The `bash` tool runs as root with no
sandboxing. Anyone who has the OAuth token has full root inside the VM, and
anyone who can prompt-inject the agent through the natural-language interface
has the same. Don't:

- Boot this ISO somewhere the network is hostile.
- Hand the ISO or the system VMDK to someone else — both have *your* OAuth
  token and *your* SSH public key baked in.
- Mount sensitive host volumes into the VM.

Do:

- If you suspect the token leaked, revoke it at the Claude Code console.
- `make wipe-system` to nuke the persistent disk and start fresh (the next
  `make run` will reinstall).

## Troubleshooting

**The VM boots into the installer every time.** The disk has no valid EFI
bootloader. Either the previous install failed mid-way, or the disk has been
wiped. Re-running the installer should fix it — but check the installer's
output on `make console` (or `/tmp/install.log` if you tail'd it) for the
underlying error.

**`make console` shows nothing after install completes.** The brain hasn't
started yet. Give it 5-15 seconds after the second boot reaches "Welcome
to Alpine" / kernel boot messages. If it still hangs, SSH in and check
`ps -ef | grep brain`.

**Brain crashes with "credit balance is too low".** That's the misleading
4xx Anthropic returns for OAuth misconfiguration. Check:
1. Token is fresh — re-run `claude setup-token` on your host
2. The system prompt prefix is intact in `anthropic_client.py:21`
3. The `anthropic-beta: oauth-2025-04-20` header is exactly correct

**Build fails with "no recognized bootloader config found".** Alpine ISO
layout changed. Check `work/iso-rw/` for the actual grub.cfg path and add
it to the loop in `build.sh`.

**Fusion error: "No PCIe slot available for Ethernet0".** The PCIe root-port
bridges aren't in `.vmx`. They're in the template — make sure your rendered
`vmware/shedos.vmx` has the `pciBridge4..7` lines.

**Mount fails with "Invalid argument".** ext4 kernel module not loaded.
Should be fixed automatically by `modprobe ext4`, but if you see this in
manual mounts, run `modprobe ext4; mount -t ext4 ...` explicitly.

## What's NOT here yet

- TLS pinning on the OAuth endpoint (uses system CA bundle)
- A live update mechanism for the brain code without `scp`
- x86_64 build (only arm64 / Apple Silicon Fusion)
- Multi-disk support (one disk, three partitions)
