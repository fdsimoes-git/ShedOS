.PHONY: iso iso-x86 run vm vm-x86 tui console qemu-run qemu-serial qemu-gui clean distclean help wipe-system

ISO     := out/shedos-installer.iso
ISO_X86 := out/shedos-installer-x86_64.iso
VMX     := vmware/shedos.vmx
VMX_X86 := vmware/shedos-x86.vmx
SYSTEM_VMDK := vmware/shedos-system.vmdk
SYSTEM_VMDK_X86 := vmware/shedos-system-x86.vmdk
SOCKET  := /tmp/shedos.serial
QEMU_DISK ?= out/shedos-disk-x86.qcow2
QEMU_MEM  ?= 2G
# Guest vCPUs. x86-on-Apple-Silicon is full TCG emulation (CPU-bound, not
# RAM-bound), so more vCPUs help multi-threaded guest work (Chromium/X) far more
# than extra RAM does. Default 4 leaves host headroom on a 6+ P-core machine;
# try QEMU_CPUS=6 to use all P-cores. (More RAM than ~4G buys nothing here.)
QEMU_CPUS ?= 4
# UEFI firmware for QEMU. ShedOS installs an EFI-only bootloader (grub
# --target=x86_64-efi --removable), matching real Intel/AMD hardware, so QEMU
# must run UEFI (OVMF) — the default SeaBIOS can't boot the installed disk.
# Auto-detected across common Homebrew/Linux locations; override with
# QEMU_OVMF=/path/to/edk2-x86_64-code.fd if your firmware lives elsewhere.
QEMU_OVMF ?= $(shell for f in \
	"$$(brew --prefix qemu 2>/dev/null)/share/qemu/edk2-x86_64-code.fd" \
	/opt/homebrew/share/qemu/edk2-x86_64-code.fd \
	/usr/local/share/qemu/edk2-x86_64-code.fd \
	/usr/share/OVMF/OVMF_CODE.fd \
	/usr/share/edk2-ovmf/x64/OVMF_CODE.fd \
	/usr/share/edk2/x64/OVMF_CODE.fd ; do \
	[ -f "$$f" ] && { echo "$$f"; break; }; done)

help:
	@echo "ShedOS — make targets:"
	@echo "  make iso           build arm64 ISO + 16GB VMware system disk (set CLAUDE_CODE_OAUTH_TOKEN to bake token)"
	@echo "  make iso-x86       build universal x86_64 ISO (QEMU/VMware + real Intel/AMD HW; SKIP_VMDK=1)"
	@echo "                     -> dd out/shedos-installer-x86_64.iso to a USB stick to install bare metal"
	@echo "  make vm            render vmware/shedos.vmx from arm64 template"
	@echo "  make vm-x86        render vmware/shedos-x86.vmx from x86_64 template"
	@echo "  make run           arm64: build + open in VMware Fusion"
	@echo "  make qemu-run      x86_64: boot ISO in QEMU and install to $(QEMU_DISK)"
	@echo "  make qemu-serial   attach to the installed system's serial chat client (headless)"
	@echo "  make qemu-gui      boot the installed system with the graphical ShedOS GUI in a window"
	@echo "                     (heavy under x86 emulation; tune with QEMU_CPUS=6 QEMU_MEM=4G)"
	@echo "  make tui           full TUI client via socat raw mode"
	@echo "  make console       raw serial pipe (nc -U) — debug-only"
	@echo "  make ssh           ssh root@<vm-ip> using the key baked into the ISO"
	@echo "  make ip            print the VM's NAT IP"
	@echo "  make wipe-system   delete the 16GB VMware system disk (next boot reinstalls)"
	@echo "  make clean         rm work/"
	@echo "  make distclean     rm work/ out/ vmware/* — full reset"

# Common ISO prerequisites. work/.token-hash is included so a change to the
# CLAUDE_CODE_OAUTH_TOKEN env var forces a rebuild — Make tracks file
# timestamps, not env vars, so without this an up-to-date ISO would silently
# ship a stale (or absent) token. See the token-stamp rule below.
ISO_DEPS := build.sh overlay $(shell find overlay -type f) installer $(shell find installer -type f) \
            config/alpine-release config/arch config/target-packages.list work/.token-hash

iso: $(ISO)

$(ISO): $(ISO_DEPS)
	./build.sh

iso-x86: $(ISO_X86)

# Build the x86_64 ISO straight to its own path via OUT_ISO so it never
# clobbers the arm64 ISO at $(ISO). Shares $(ISO_DEPS) so it rebuilds on source
# (or token) changes and so qemu-run/vm-x86, which depend on $(ISO_X86),
# resolve from a clean tree.
$(ISO_X86): $(ISO_DEPS)
	OUT_ISO=$(ISO_X86) ARCH=x86_64 SKIP_VMDK=1 ./build.sh

# Materialize the OAuth token's state into a stamp file so the ISO targets can
# depend on it (Make can't depend on an env var directly). Only a SHA-256 of
# the token is written — to gitignored work/, never the token itself. The
# stamp's mtime changes ONLY when the token changes, so unchanged tokens don't
# trigger needless rebuilds; the FORCE prerequisite re-checks on every build.
work/.token-hash: FORCE
	@mkdir -p work
	@printf '%s' "$$CLAUDE_CODE_OAUTH_TOKEN" | shasum -a 256 | awk '{print $$1}' > $@.tmp
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv $@.tmp $@; fi

FORCE:
.PHONY: FORCE

vm: $(VMX)

$(VMX): vmware/shedos.vmx.tmpl $(ISO)
	cp vmware/shedos.vmx.tmpl $(VMX)

vm-x86: $(VMX_X86)

# iso-x86 builds with SKIP_VMDK=1 (the ISO build shouldn't require Fusion), so
# the x86 system disk is created here instead, via Fusion's vmware-vdiskmanager.
# A dedicated x86 disk (not the arm64 shedos-system.vmdk) avoids a cross-arch
# clash. NOTE: x86 VMware guests run only on Intel Macs; on Apple Silicon use
# 'make qemu-run' instead.
$(VMX_X86): vmware/shedos-x86.vmx.tmpl $(ISO_X86)
	@if [ ! -f $(SYSTEM_VMDK_X86) ]; then \
		vdm="/Applications/VMware Fusion.app/Contents/Library/vmware-vdiskmanager"; \
		if [ -x "$$vdm" ]; then \
			echo "[vm-x86] creating $(SYSTEM_VMDK_X86) (16 GB)"; \
			"$$vdm" -c -s 16GB -a lsilogic -t 0 $(SYSTEM_VMDK_X86) >/dev/null \
				|| { echo "vmware-vdiskmanager failed to create $(SYSTEM_VMDK_X86)"; exit 1; }; \
		else \
			echo "vmware-vdiskmanager not found — install VMware Fusion to create $(SYSTEM_VMDK_X86)."; \
			echo "(x86 VMware guests run only on Intel Macs; on Apple Silicon use 'make qemu-run'.)"; \
			exit 1; \
		fi; \
	fi
	cp vmware/shedos-x86.vmx.tmpl $(VMX_X86)

run: $(ISO) $(VMX)
	./vmware/launch.sh

qemu-run: $(ISO_X86)
	@command -v qemu-system-x86_64 >/dev/null 2>&1 || { \
		echo "qemu-system-x86_64 not found — run: brew install qemu (Mac) or apt install qemu-system-x86 (Linux)"; \
		exit 1; \
	}
	@if [ -z "$(QEMU_OVMF)" ]; then \
		echo "no OVMF/UEFI firmware found — install it (brew install qemu, or your distro's"; \
		echo "ovmf/edk2-ovmf package) or pass QEMU_OVMF=/path/to/edk2-x86_64-code.fd."; \
		echo "ShedOS installs an EFI-only bootloader, so QEMU must run UEFI."; \
		exit 1; \
	fi
	@if [ ! -f $(QEMU_DISK) ]; then \
		echo "[qemu] creating $(QEMU_DISK)"; \
		qemu-img create -f qcow2 $(QEMU_DISK) 16G; \
	fi
	@echo "[qemu] firmware: $(QEMU_OVMF)"
	@echo "[qemu] the wizard appears in the QEMU window (tty1); the install log also"
	@echo "[qemu] streams here (serial). After install+reboot run 'make qemu-serial'."
	qemu-system-x86_64 \
		-M q35 -m $(QEMU_MEM) -smp $(QEMU_CPUS) \
		-drive if=pflash,format=raw,readonly=on,file=$(QEMU_OVMF) \
		-cdrom $(ISO_X86) \
		-drive file=$(QEMU_DISK),format=qcow2,if=virtio \
		-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
		-serial stdio \
		-boot order=dc

qemu-serial:
	@if [ -z "$(QEMU_OVMF)" ]; then \
		echo "no OVMF/UEFI firmware found — the installed disk is EFI-only and won't boot"; \
		echo "under SeaBIOS. Install OVMF or pass QEMU_OVMF=/path/to/edk2-x86_64-code.fd."; \
		exit 1; \
	fi
	@if [ ! -f $(QEMU_DISK) ]; then \
		echo "$(QEMU_DISK) not found — run 'make qemu-run' first to install"; \
		exit 1; \
	fi
	@echo "[qemu] booting installed disk (UEFI: $(QEMU_OVMF)) — serial on stdio"
	qemu-system-x86_64 \
		-M q35 -m $(QEMU_MEM) -smp $(QEMU_CPUS) \
		-drive if=pflash,format=raw,readonly=on,file=$(QEMU_OVMF) \
		-drive file=$(QEMU_DISK),format=qcow2,if=virtio \
		-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
		-serial stdio \
		-display none

# Same as qemu-serial but with a graphical window so you can see the actual
# ShedOS GUI (Chromium kiosk on tty1), not just the serial chat client.
#   -vga virtio   : virtio-gpu — the guest's built-in `modesetting` Xorg driver
#                   drives it via the virtio_gpu KMS module (in linux-lts).
#   qemu-xhci + usb-tablet : absolute pointer so the mouse works without grab.
#   -serial stdio : kept, so boot/log + the chat client are still on this term.
# NOTE: x86 QEMU on Apple Silicon is full TCG emulation, so Chromium will be
# SLOW (it renders, but don't expect snappy interaction — the serial chat is
# far more responsive). Bump RAM with QEMU_MEM=4G for the GUI.
qemu-gui:
	@if [ -z "$(QEMU_OVMF)" ]; then \
		echo "no OVMF/UEFI firmware found — the installed disk is EFI-only and won't boot"; \
		echo "under SeaBIOS. Install OVMF or pass QEMU_OVMF=/path/to/edk2-x86_64-code.fd."; \
		exit 1; \
	fi
	@if [ ! -f $(QEMU_DISK) ]; then \
		echo "$(QEMU_DISK) not found — run 'make qemu-run' first to install"; \
		exit 1; \
	fi
	@echo "[qemu] booting installed disk with GUI (virtio-gpu) — SLOW under emulation."
	@echo "[qemu] GUI is in the QEMU window; serial/log + chat client on this terminal."
	qemu-system-x86_64 \
		-M q35 -m $(QEMU_MEM) -smp $(QEMU_CPUS) \
		-drive if=pflash,format=raw,readonly=on,file=$(QEMU_OVMF) \
		-drive file=$(QEMU_DISK),format=qcow2,if=virtio \
		-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
		-vga virtio \
		-device qemu-xhci -device usb-tablet \
		-serial stdio

tui:
	@if [ ! -S $(SOCKET) ]; then \
		echo "$(SOCKET) doesn't exist yet — start the VM with 'make run' first."; \
		exit 1; \
	fi
	@command -v socat >/dev/null 2>&1 || { \
		echo "socat not installed — run: brew install socat"; \
		echo "(socat puts your terminal into raw mode so the chat client's keys + colors work)"; \
		exit 1; \
	}
	@echo "Connecting to ShedOS chat. Press Ctrl-]  Ctrl-C to quit."
	@socat -,rawer,escape=0x1d UNIX-CONNECT:$(SOCKET)

console:
	@if [ ! -S $(SOCKET) ]; then \
		echo "$(SOCKET) doesn't exist yet — start the VM with 'make run' first."; \
		exit 1; \
	fi
	@echo "Raw serial console (ttyS0 runs the chat client — for the proper"
	@echo "client use 'make tui' which sets up a PTY via socat). Ctrl-C to quit."
	@nc -U $(SOCKET)

ip:
	@vmrun getGuestIPAddress $(VMX) 2>/dev/null || echo "VM not running or guest tools missing"

ssh:
	@ip=$$(vmrun getGuestIPAddress $(VMX) 2>/dev/null); \
	 if [ -z "$$ip" ]; then echo "VM not running"; exit 1; fi; \
	 echo "ssh root@$$ip"; \
	 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@$$ip

wipe-system:
	@echo "Stopping VM..."
	@vmrun stop $(VMX) hard 2>/dev/null || true
	rm -f $(SYSTEM_VMDK) vmware/shedos-system-*.vmdk
	# VMware .lck artifacts are directories, not files — needs -rf.
	rm -rf vmware/shedos-system.vmdk.lck vmware/*.lck
	@echo "System disk wiped. Next 'make run' rebuilds the ISO (which recreates"
	@echo "the empty VMDK), VM boots into the installer, which reinstalls Alpine."

clean:
	rm -rf work

distclean: clean wipe-system
	rm -rf out
	rm -f vmware/shedos.vmx vmware/shedos-console.log vmware/*.vmdk vmware/*.nvram vmware/*.vmsd vmware/*.vmem
