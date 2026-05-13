.PHONY: iso run vm console clean distclean help wipe-system

ISO := out/shedos-installer.iso
VMX := vmware/shedos.vmx
SYSTEM_VMDK := vmware/shedos-system.vmdk
SOCKET := /tmp/shedos.serial

help:
	@echo "ShedOS — make targets:"
	@echo "  make iso          build out/shedos-installer.iso + create 16GB system vmdk"
	@echo "                    (set CLAUDE_CODE_OAUTH_TOKEN before to bake it in)"
	@echo "  make vm           render vmware/shedos.vmx from template"
	@echo "  make run          build + open in VMware Fusion (auto-installs on first boot)"
	@echo "  make console      connect to the brain via the serial socket (nc -U)"
	@echo "  make ssh          ssh root@<vm-ip> using the key baked into the ISO"
	@echo "  make ip           print the VM's NAT IP"
	@echo "  make wipe-system  delete the 16GB system disk (next boot reinstalls)"
	@echo "  make clean        rm work/"
	@echo "  make distclean    rm work/ out/ vmware/* — full reset"

iso: $(ISO)

$(ISO): build.sh overlay $(shell find overlay -type f) installer $(shell find installer -type f) config/alpine-release config/arch config/target-packages.list
	./build.sh

vm: $(VMX)

$(VMX): vmware/shedos.vmx.tmpl $(ISO)
	cp vmware/shedos.vmx.tmpl $(VMX)

run: $(ISO) $(VMX)
	./vmware/launch.sh

console:
	@if [ ! -S $(SOCKET) ]; then \
		echo "$(SOCKET) doesn't exist yet — start the VM with 'make run' first."; \
		echo "(Fusion creates the socket on VM power-on. The socket lives in"; \
		echo " /tmp because /Volumes/Untitled doesn't support Unix sockets.)"; \
		exit 1; \
	fi
	@echo "Connecting to ShedOS brain via serial. Ctrl-C to quit."
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
	rm -f $(SYSTEM_VMDK) vmware/shedos-system-*.vmdk vmware/shedos-system.vmdk.lck
	@echo "System disk wiped. Next 'make run' rebuilds the ISO (which recreates"
	@echo "the empty VMDK), VM boots into the installer, which reinstalls Alpine."

clean:
	rm -rf work

distclean: clean wipe-system
	rm -rf out
	rm -f vmware/shedos.vmx vmware/shedos-console.log vmware/*.vmdk vmware/*.nvram vmware/*.vmsd vmware/*.vmem
