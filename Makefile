.PHONY: iso iso-x86 run vm vm-x86 tui console qemu-run qemu-serial clean distclean help wipe-system

ISO     := out/shedos-installer.iso
ISO_X86 := out/shedos-installer-x86_64.iso
VMX     := vmware/shedos.vmx
VMX_X86 := vmware/shedos-x86.vmx
SYSTEM_VMDK := vmware/shedos-system.vmdk
SOCKET  := /tmp/shedos.serial
QEMU_DISK ?= out/shedos-disk-x86.qcow2
QEMU_MEM  ?= 2G

help:
	@echo "ShedOS — make targets:"
	@echo "  make iso           build arm64 ISO + 16GB VMware system disk (set CLAUDE_CODE_OAUTH_TOKEN to bake token)"
	@echo "  make iso-x86       build x86_64 ISO for QEMU / Intel hardware (SKIP_VMDK=1)"
	@echo "  make vm            render vmware/shedos.vmx from arm64 template"
	@echo "  make vm-x86        render vmware/shedos-x86.vmx from x86_64 template"
	@echo "  make run           arm64: build + open in VMware Fusion"
	@echo "  make qemu-run      x86_64: boot ISO in QEMU and install to $(QEMU_DISK)"
	@echo "  make qemu-serial   attach to QEMU serial console (after install + reboot)"
	@echo "  make tui           full TUI client via socat raw mode"
	@echo "  make console       raw serial pipe (nc -U) — debug-only"
	@echo "  make ssh           ssh root@<vm-ip> using the key baked into the ISO"
	@echo "  make ip            print the VM's NAT IP"
	@echo "  make wipe-system   delete the 16GB VMware system disk (next boot reinstalls)"
	@echo "  make clean         rm work/"
	@echo "  make distclean     rm work/ out/ vmware/* — full reset"

iso: $(ISO)

$(ISO): build.sh overlay $(shell find overlay -type f) installer $(shell find installer -type f) config/alpine-release config/arch config/target-packages.list
	./build.sh

iso-x86:
	ARCH=x86_64 SKIP_VMDK=1 ./build.sh
	@if [ -f out/shedos-installer.iso ]; then cp out/shedos-installer.iso $(ISO_X86); echo "[make] x86_64 ISO also at $(ISO_X86)"; fi

vm: $(VMX)

$(VMX): vmware/shedos.vmx.tmpl $(ISO)
	cp vmware/shedos.vmx.tmpl $(VMX)

vm-x86: $(VMX_X86)

$(VMX_X86): vmware/shedos-x86.vmx.tmpl $(ISO_X86)
	cp vmware/shedos-x86.vmx.tmpl $(VMX_X86)

run: $(ISO) $(VMX)
	./vmware/launch.sh

qemu-run: $(ISO_X86)
	@command -v qemu-system-x86_64 >/dev/null 2>&1 || { \
		echo "qemu-system-x86_64 not found — run: brew install qemu (Mac) or apt install qemu-system-x86 (Linux)"; \
		exit 1; \
	}
	@if [ ! -f $(QEMU_DISK) ]; then \
		echo "[qemu] creating $(QEMU_DISK)"; \
		qemu-img create -f qcow2 $(QEMU_DISK) 16G; \
	fi
	@echo "[qemu] booting installer ISO — after install+reboot use 'make qemu-serial' to connect"
	qemu-system-x86_64 \
		-M q35 -m $(QEMU_MEM) \
		-cdrom $(ISO_X86) \
		-drive file=$(QEMU_DISK),format=qcow2,if=virtio \
		-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
		-serial stdio \
		-display none \
		-boot order=dc

qemu-serial:
	@if [ ! -f $(QEMU_DISK) ]; then \
		echo "$(QEMU_DISK) not found — run 'make qemu-run' first to install"; \
		exit 1; \
	fi
	@echo "[qemu] booting installed disk — serial on stdio"
	qemu-system-x86_64 \
		-M q35 -m $(QEMU_MEM) \
		-drive file=$(QEMU_DISK),format=qcow2,if=virtio \
		-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
		-serial stdio \
		-display none

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
