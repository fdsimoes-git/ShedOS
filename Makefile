.PHONY: iso run vm clean distclean help

ISO := out/shedos.iso
VMX := vmware/shedos.vmx

help:
	@echo "ShedOS — make targets:"
	@echo "  make iso        build out/shedos.iso (set CLAUDE_CODE_OAUTH_TOKEN to bake it in)"
	@echo "  make vm         render vmware/shedos.vmx from template"
	@echo "  make run        build ISO + vmx, open in VMware Fusion"
	@echo "  make ssh        ssh root@<vm-ip> using the key baked into the ISO"
	@echo "  make ip         print the VM's NAT IP via vmrun"
	@echo "  make clean      rm work/"
	@echo "  make distclean  rm work/ out/ vmware/shedos.vmx"

iso: $(ISO)

$(ISO): build.sh overlay $(shell find overlay -type f) config/alpine-release config/arch
	./build.sh

vm: $(VMX)

$(VMX): vmware/shedos.vmx.tmpl $(ISO)
	cp vmware/shedos.vmx.tmpl $(VMX)

run: $(ISO) $(VMX)
	./vmware/launch.sh

ip:
	@vmrun getGuestIPAddress $(VMX) 2>/dev/null || echo "VM not running or guest tools missing"

ssh:
	@ip=$$(vmrun getGuestIPAddress $(VMX) 2>/dev/null); \
	 if [ -z "$$ip" ]; then echo "VM not running"; exit 1; fi; \
	 echo "ssh root@$$ip"; \
	 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@$$ip

clean:
	rm -rf work

distclean: clean
	rm -rf out
	rm -f vmware/shedos.vmx vmware/shedos-console.log vmware/*.vmdk vmware/*.nvram vmware/*.vmsd vmware/*.vmem
