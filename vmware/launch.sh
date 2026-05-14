#!/usr/bin/env bash
# Open ShedOS in VMware Fusion (or start it headless if Fusion isn't running).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VMX="$HERE/shedos.vmx"

if [[ ! -f "$VMX" ]]; then
    echo "[launch] vmware/shedos.vmx missing — run 'make vm' first" >&2
    exit 1
fi

ISO="$HERE/../out/shedos-installer.iso"
if [[ ! -f "$ISO" ]]; then
    echo "[launch] out/shedos-installer.iso missing — run 'make iso' first" >&2
    exit 1
fi

if command -v vmrun >/dev/null 2>&1; then
    echo "[launch] vmrun start $VMX gui"
    vmrun start "$VMX" gui
else
    echo "[launch] vmrun not in PATH, falling back to 'open'"
    open "$VMX"
fi
