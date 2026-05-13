.PHONY: iso run vm console wipe-data clean distclean help

ISO := out/shedos.iso
VMX := vmware/shedos.vmx

help:
	@echo "ShedOS — make targets:"
	@echo "  make iso        build out/shedos.iso (set CLAUDE_CODE_OAUTH_TOKEN to bake it in)"
	@echo "  make vm         render vmware/shedos.vmx from template"
	@echo "  make run        build ISO + vmx, open in VMware Fusion"
	@echo "  make console    connect to the brain via the serial socket (nc -U)"
	@echo "  make ssh        ssh root@<vm-ip> using the key baked into the ISO"
	@echo "  make ip         print the VM's NAT IP via vmrun"
	@echo "  make wipe-data  delete the /data VMDK (next boot reinitializes)"
	@echo "  make clean      rm work/"
	@echo "  make distclean  rm work/ out/ vmware/shedos.vmx + data VMDK"

iso: $(ISO)

$(ISO): build.sh overlay $(shell find overlay -type f) config/alpine-release config/arch
	./build.sh

vm: $(VMX)

$(VMX): vmware/shedos.vmx.tmpl $(ISO)
	cp vmware/shedos.vmx.tmpl $(VMX)

run: $(ISO) $(VMX)
	./vmware/launch.sh

SOCKET := /tmp/shedos.serial

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

wipe-data:
	@echo "Stopping VM..."
	@vmrun stop vmware/shedos.vmx hard 2>/dev/null || true
	rm -f vmware/shedos-data.vmdk vmware/shedos-data-*.vmdk vmware/shedos-data.vmdk.lck
	@echo "Persistent /data wiped. Next boot will reinitialize (fresh ext4)."

clean:
	rm -rf work

distclean: clean wipe-data
	rm -rf out
	rm -f vmware/shedos.vmx vmware/shedos-console.log vmware/*.vmdk vmware/*.nvram vmware/*.vmsd vmware/*.vmem
