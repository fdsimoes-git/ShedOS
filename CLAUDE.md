# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

ShedOS is an Alpine-Linux–based custom OS for VMware Fusion arm64 (Apple Silicon) where a Python agent (`brain.py`) talking to the Claude API is the **only** user-facing process. The user types natural language at `tty1` (Fusion window) or `ttyS0` (Unix-socket pipe `/tmp/shedos.serial`), and the agent dispatches `bash`/`apk`/`read_file`/`write_file`/`list_dir`/`process_list`/`process_kill`/`net_fetch` tools.

Authentication uses the Claude Code OAuth flow (Bearer + `anthropic-beta: oauth-2025-04-20`). The system prompt **must** start with `"You are Claude Code, Anthropic's official CLI for Claude."` — without that prefix, Opus rejects with a misleading "credit balance" 4xx. See `overlay/opt/shedos/anthropic_client.py`.

## Branch matters — two architectures

The architecture is fundamentally different between the two main branches:

| Branch | Model | Root FS | Persistence |
|---|---|---|---|
| `main` (v0.1.0) | Alpine **diskless mode** + apkovl | tmpfs | Nothing survives reboot except what's baked into the ISO |
| `feature/persistent-install` (v0.2.0 candidate, PR #3) | Real Alpine **install** to /dev/sda via a one-shot installer ISO | ext4 on /dev/sda2 (`/`) + ext4 on /dev/sda3 (`/home`) | Everything persists; brain history at `/var/lib/shedos/brain-<tty>.jsonl` |

**Always check `git branch --show-current` before working** — `overlay/` means different things on each branch (apkovl contents vs target-system files), and the build pipeline is structurally different.

## Common commands

```bash
# Token in env is required for the brain to authenticate. Get one with `claude setup-token`.
export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...'

make iso          # builds out/shedos[-installer].iso (+ creates 16GB system VMDK on persistent-install)
make run          # builds + boots the VM in Fusion (handles vmx render too)
make console      # connect to brain on /tmp/shedos.serial via nc -U
make ssh          # ssh root@<vm-ip> using ~/.ssh/id_ed25519
make ip           # vmrun getGuestIPAddress
make wipe-system  # (persistent-install only) delete the system VMDK; next boot reinstalls
make distclean    # reset everything
```

Iteration on the brain code without rebuilding the ISO:
```bash
scp overlay/opt/shedos/brain.py root@<vm-ip>:/opt/shedos/brain.py
ssh root@<vm-ip> 'pkill -f brain.py'   # getty respawns it with the new code
```

## Build pipeline shape

`build.sh` does the actual work; `make iso` just invokes it. Key non-obvious steps:

- Downloads `alpine-virt-3.23.0-aarch64.iso` and **patches its grub.cfg** to add `apkovl=sr0:iso9660:/<name>.apkovl.tar.gz` (use `sr0`, NOT `cdrom` — the latter is a userspace symlink that doesn't exist in initramfs)
- Rewrites `console=ttyAMA0` to `console=ttyS0,115200` because Fusion emulates an 8250-style UART, not ARM PL011
- Tar uses `--uid 0 --gid 0 --uname root --gname root` so apkovl files end up owned by root in the guest (otherwise sshd's StrictModes rejects /root/.ssh/authorized_keys)
- On `feature/persistent-install`: also writes `rootfstype=ext4 rootwait` to GRUB_CMDLINE_LINUX_DEFAULT — busybox `mount` can't autodetect a filesystem from `UUID=...`, so without `rootfstype=ext4` the initramfs panics with a misleading "No such file or directory" trying to mount root
- Pre-creates `vmware/shedos-system.vmdk` (16 GB growable) via `/Applications/VMware Fusion.app/Contents/Library/vmware-vdiskmanager` if missing

## VMware Fusion arm64 quirks (all encoded in `vmware/shedos.vmx.tmpl`)

These are NOT auto-allocated on a minimal handcrafted .vmx. Don't remove them:

- `pciBridge0..7.present = "TRUE"` + `pciBridge4..7.virtualDev = "pcieRootPort"` + `ethernet0.pciSlotNumber = "160"` — without these you get `No PCIe slot available for Ethernet0`
- `usb.present`, `ehci.present`, `usb_xhci.present` — without these Fusion presents no USB bus at all → no virtual keyboard/mouse → keystrokes typed into the Fusion window go nowhere even though the framebuffer renders fine
- `serial0.fileType = "pipe"` + `serial0.fileName = "/tmp/shedos.serial"` — the path **must** be on APFS, NOT this project directory. `/Volumes/Untitled` is exFAT and can't host AF_UNIX sockets; bind() fails with `Operation not supported`
- `monitor.allowLegacyCPU = "TRUE"` + `firmware = "efi"` + `guestOS = "arm-other-64"` — required for arm64 guests on Apple Silicon

## Repo layout

```
shedos/
├── build.sh              build pipeline; produces out/shedos[-installer].iso
├── Makefile              wrappers around build.sh + vmrun
├── config/               pinned alpine version (3.23.0), arch (aarch64), target-packages.list
├── overlay/              what gets installed on the target system
│   ├── etc/              inittab (brain on tty1+ttyS0), sshd_config, persona, shedos-brain init.d, local.d
│   └── opt/shedos/       brain.py, tools.py, anthropic_client.py, config.py, ui.py, run-brain.sh
├── installer/            (persistent-install only) apkovl on the live installer ISO
│   ├── etc/              inittab runs installer.sh on ttyS0; tty1 shows banner
│   └── opt/shedos-installer/installer.sh   parted + mkfs + apk --root + chroot grub-install
├── vmware/               .vmx template + launch.sh + (gitignored) Fusion runtime files
└── out/                  (gitignored) shedos-installer.iso
```

`brain.py` runs as **two independent processes**, one per tty (tty1 in Fusion window, ttyS0 over the host pipe). They have separate `messages` lists and separate persistence files (`brain-tty1.jsonl` vs `brain-ttyS0.jsonl`) — what you say in one doesn't reach the other.

## When the install gets stuck (persistent-install only)

If the post-install boot drops to an initramfs `~ #` rescue shell, you can interact via `nc -U /tmp/shedos.serial`. Most likely causes (all should be fixed in current main, but worth knowing):

1. `mount: mounting /dev/sda2 on /sysroot failed` — `rootfstype=ext4` missing from kernel cmdline (busybox mount can't autodetect from UUID=)
2. Initramfs gives up before SATA detects — `rootwait` missing
3. `/etc/fstab` has a malformed `UUID=...` line — busybox `blkid -s UUID -o value` silently dropped the flags; parse with sed instead

From the rescue shell: `mount -t ext4 /dev/sda2 /sysroot`, edit `/sysroot/boot/grub/grub.cfg` and `/sysroot/etc/default/grub` to add `rootfstype=ext4 rootwait` to the kernel cmdline, fix `/sysroot/etc/fstab`, then `echo b > /proc/sysrq-trigger` to reboot.

## Branch protection & merging

`main` is protected: PRs require @fdsimoes-git's review, but admin/owner bypass is enabled. To merge a PR using bypass:

```bash
gh pr merge <N> --merge --admin --delete-branch
```

## Threat model

ShedOS is a **single-user appliance**. The `bash` tool runs as root with no sandbox. Anyone with the OAuth token has full root in the VM, and so does anyone who can prompt-inject the agent. **Don't** hand out the ISO or the system VMDK — both have the build host's OAuth token and SSH public key baked in. Don't boot it on a hostile network. Don't mount sensitive host volumes.
