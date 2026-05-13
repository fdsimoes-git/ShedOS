# ShedOS

A minimal Linux distro where **Claude is the only interface**. You boot the ISO
in VMware Fusion, tty1 shows a `>` prompt, and you talk to it in natural
language. Under the hood, Claude Opus 4.6 runs as the user-facing process,
calling shell/file/network tools to do whatever you ask.

Think OpenClaw, but instead of running on top of an OS, the agent *is* the
shell of a self-contained boot image.

## How it works

```
┌─ shedos.iso ────────────────────────────────────────────────┐
│  Alpine 3.23 kernel + initramfs (unchanged)                 │
│  grub.cfg ← patched with apkovl=cdrom:iso9660:/shedos…      │
│  shedos.apkovl.tar.gz ← our overlay                         │
│    ├ /etc/             OpenRC service, sshd, network, …     │
│    ├ /root/.ssh/       authorized_keys (your host pubkey)   │
│    └ /opt/shedos/      brain.py + tools.py + httpx client   │
└─────────────────────────────────────────────────────────────┘

Boot: UEFI → GRUB → kernel + initramfs → nlplug-findfs reads the
apkovl from the ISO → unpacks it onto a tmpfs root → OpenRC →
local.d installs python3+sshd via apk add → brain.py owns tty1.
```

The agent uses the same OAuth pattern as Claude Code: `Bearer` auth,
`anthropic-beta: oauth-2025-04-20`, and a system prompt that starts with the
Claude Code identity block.

## Requirements (host)

- macOS on Apple Silicon (the Alpine ISO and `.vmx` are arm64)
- VMware Fusion 13+
- `xorriso`, `coreutils`, `gnu-tar` — `brew install xorriso coreutils gnu-tar`
- An SSH keypair at `~/.ssh/id_ed25519` (or `id_rsa`, or `id_ecdsa`)
- A Claude Code OAuth token. Get one with `claude setup-token` from a host
  that has Claude Code installed. Token starts with `sk-ant-oat01-`.

## Build & boot

```bash
# Bake the token into the ISO at build time (recommended path)
export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
make run            # builds out/shedos.iso, renders vmware/shedos.vmx, opens Fusion
```

Or skip baking and let ShedOS prompt for the token on tty1 the first time it
boots:

```bash
unset CLAUDE_CODE_OAUTH_TOKEN
make run
# In the Fusion console, paste the token at the "Token:" prompt.
```

The boot takes ~30s on first run (apk has to pull python3 and openssh from
dl-cdn.alpinelinux.org). Subsequent boots are the same — root is tmpfs, so
nothing persists across reboots except what's baked into the ISO.

After it's up:

```bash
make ip            # prints the VM's NAT IP
make ssh           # ssh root@<that ip> using your key
```

SSH is the escape hatch. tty2–tty6 in the Fusion console give you a normal
root shell if the brain wedges (`Ctrl-Cmd-2` etc. in Fusion).

## Talking to it

At the `>` prompt:

```
> what's my IP?
[tool: bash(command="ip -4 addr show eth0")]
192.168.65.4

> install htop and run it once
[tool: apk(args="add htop")]
[tool: bash(command="htop -n 1")]
…rendered output…
```

Claude has these tools available:

| tool | purpose |
|---|---|
| `bash` | run a shell command as root |
| `apk` | Alpine package manager (alias for `bash` but explicit) |
| `read_file`, `write_file`, `list_dir` | filesystem |
| `process_list`, `process_kill` | process control |
| `net_fetch` | HTTP GET |

## Repo layout

```
shedos/
├── README.md          (this file)
├── Makefile           targets: iso, vm, run, ssh, ip, clean
├── build.sh           the ISO build pipeline
├── config/            pinned Alpine version, arch, extra packages
├── overlay/           becomes shedos.apkovl.tar.gz
│   ├── etc/           OpenRC service, sshd, network, inittab, persona
│   └── opt/shedos/    brain.py + tools.py + httpx client + ui
├── vmware/            Fusion .vmx template + launch script
└── out/               build output (gitignored)
```

## Threat model — read this

ShedOS is a **single-user appliance**. The `bash` tool runs as root with no
sandboxing. Anyone who has the OAuth token has full root inside the VM, and
anyone who can prompt-inject Claude through the natural-language interface
has the same. Don't:

- Boot this ISO somewhere the network is hostile.
- Hand the ISO to someone else — it has *your* OAuth token and *your* SSH
  public key baked in.
- Mount sensitive host volumes into the VM.

Do:

- Treat the VM as ephemeral. Reboot wipes tmpfs. That's a feature.
- If you suspect the token leaked, revoke it at the Claude Code console.

## Troubleshooting

**The VM boots but tty1 shows a regular login prompt, not the ShedOS banner.**
The apkovl didn't apply. SSH in and check `/etc/shedos/` exists. If it
doesn't, the `apkovl=` kernel arg didn't take. Look at
`vmware/shedos-console.log` for kernel cmdline. Fallback: drop the apkovl
in as `localhost.apkovl.tar.gz` (Alpine auto-discovers by hostname).

**The brain crashes with "credit balance is too low".**
That's the misleading 4xx Anthropic returns for OAuth misconfiguration.
Three things to check:
1. Token is fresh — re-run `claude setup-token` on your host.
2. System prompt starts with the Claude Code identity block (it does in
   `anthropic_client.py:21` — don't change that).
3. Header is `anthropic-beta: oauth-2025-04-20` exactly.

**Build fails with "no recognized bootloader config found".**
The Alpine ISO layout changed. Check `work/iso-rw/` for the actual paths
and add them to the loop in `build.sh:9`.

**Networking is dead.**
`make ssh` won't work until the local.d/shedos-packages.start completes
the initial `apk add openssh`. Give it 30–60s after the kernel boots.

## Persistence — `/data`

Root is tmpfs by design (Alpine diskless mode + apkovl). To survive reboots
ShedOS attaches a separate **4GB ext4 disk** mounted at `/data`:

- `vmware/shedos-data.vmdk` is auto-created on first `make iso` via
  `vmware-vdiskmanager`. It persists across `make iso` runs — the build
  pipeline only touches it if it doesn't exist.
- On first boot, `shedos-data` OpenRC service GPT-partitions and ext4-formats
  the disk, then mounts it at `/data`. Subsequent boots just mount.
- Each brain auto-persists its conversation history to
  `/data/shedos/brain-<tty>.jsonl` (appended per message). On reboot, the
  brain reloads the history and you continue the same conversation.

When the agent wants to save anything long-lived (logs, datasets, scripts,
state) the persona tells it to put it under `/data`.

To wipe `/data` and start fresh:
```bash
make wipe-data
```

## What's NOT here yet

- TLS for the OAuth endpoint pinning — relies on system CA bundle
- A way to update the brain without rebuilding the ISO (you can `scp` new
  files into `/opt/shedos/` and `pkill -f brain.py` to make getty respawn
  with the new code)
- x86_64 build (only arm64 / Apple Silicon Fusion)
