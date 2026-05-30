# bare-metal/Makefile — build ShedOS bare-metal kernel
#
# Requires a freestanding x86_64 cross-compiler:
#   macOS:  brew install x86_64-elf-gcc nasm
#   Linux:  apt install gcc-x86-64-linux-gnu nasm
#           (or build x86_64-elf-gcc from source)
#
# Optional: clang cross-compile alternative:
#   CC=clang CFLAGS_EXTRA="--target=x86_64-elf" make
#
# Bake the OAuth token at compile time:
#   make CLAUDE_TOKEN=sk-ant-oat01-...
#
# Run in QEMU:
#   make qemu         (from ISO, installer-style)
#   make qemu-kernel  (direct -kernel, faster iteration)

.PHONY: all clean qemu qemu-kernel iso

# ── Toolchain ─────────────────────────────────────────────────────────────────
# Try x86_64-elf-gcc first (cross-compiler), fall back to system gcc/clang
# with explicit -m64 and freestanding flags.
ifneq ($(shell command -v x86_64-elf-gcc 2>/dev/null),)
    CC  := x86_64-elf-gcc
    LD  := x86_64-elf-ld
    OC  := x86_64-elf-objcopy
else ifneq ($(shell command -v x86_64-linux-gnu-gcc 2>/dev/null),)
    CC  := x86_64-linux-gnu-gcc
    LD  := x86_64-linux-gnu-ld
    OC  := x86_64-linux-gnu-objcopy
else
    CC  := clang
    LD  := ld
    OC  := objcopy
    CFLAGS_EXTRA += --target=x86_64-elf
endif

ASM := nasm

# grub-mkrescue is unprefixed on Linux (and bundles the i386-pc BIOS modules,
# so the rescue ISO boots under QEMU's default SeaBIOS). On macOS install a
# cross GRUB via Homebrew; prefer i686-elf-grub (i386-pc/BIOS) because the
# qemu target below boots with SeaBIOS — x86_64-elf-grub only ships the
# x86_64-efi platform, whose Multiboot2 loader needs UEFI + a framebuffer tag.
ifneq ($(shell command -v grub-mkrescue 2>/dev/null),)
    GRUB_MKRESCUE := grub-mkrescue
else ifneq ($(shell command -v i686-elf-grub-mkrescue 2>/dev/null),)
    GRUB_MKRESCUE := i686-elf-grub-mkrescue
else ifneq ($(shell command -v x86_64-elf-grub-mkrescue 2>/dev/null),)
    GRUB_MKRESCUE := x86_64-elf-grub-mkrescue
else
    GRUB_MKRESCUE := grub-mkrescue
endif

# ── Flags ─────────────────────────────────────────────────────────────────────
# -mno-sse/sse2/mmx/80387: the kernel never enables SSE (no OSFXSR in CR4),
# so let the compiler auto-vectorize memset/memcpy with XMM at -O2 and the
# first such instruction #UDs. Forbid FP/vector codegen entirely.
CFLAGS := -std=c11 -ffreestanding -fno-stack-protector -fno-pic \
           -mno-red-zone -mno-sse -mno-sse2 -mno-mmx -mno-80387 \
           -m64 -Wall -Wextra -O2 \
           -nostdinc -I./include \
           $(CFLAGS_EXTRA)

ifdef CLAUDE_TOKEN
    CFLAGS += -DCLAUDE_TOKEN='"$(CLAUDE_TOKEN)"'
endif

LDFLAGS := -T boot/linker.ld -nostdlib
ASMFLAGS := -f elf64

OUT := out

# ── Source files ──────────────────────────────────────────────────────────────
ASM_SRCS := boot/entry.asm

C_SRCS := \
    kernel/main.c \
    drivers/uart.c \
    drivers/pci.c \
    drivers/virtio_net.c \
    drivers/rtc.c \
    net/net.c \
    net/dns.c \
    net/tcp.c \
    net/tls.c \
    crypto/sha256.c \
    crypto/sha512.c \
    crypto/chacha20poly1305.c \
    crypto/x25519.c \
    crypto/ecdsa.c \
    crypto/selftest.c \
    x509/x509.c \
    mm/pmm.c \
    mm/heap.c \
    lib/printf.c \
    lib/string.c \
    claude/chat.c \
    claude/client.c \
    claude/session.c \
    http/client.c

# TODO Phase 2c-e: add as implemented:
#   net/dns.c  net/tcp.c  net/tls.c  http/client.c  claude/client.c

ASM_OBJS := $(patsubst %.asm,$(OUT)/%.o,$(ASM_SRCS))
C_OBJS   := $(patsubst %.c,$(OUT)/%.o,$(C_SRCS))
OBJS     := $(ASM_OBJS) $(C_OBJS)

KERNEL := $(OUT)/kernel.elf

# ── Build rules ───────────────────────────────────────────────────────────────
all: $(KERNEL)

$(OUT)/%.o: %.asm | $(OUT)
	@mkdir -p $(dir $@)
	$(ASM) $(ASMFLAGS) $< -o $@

$(OUT)/%.o: %.c | $(OUT)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

$(KERNEL): $(OBJS)
	$(LD) $(LDFLAGS) -o $@ $^
	@echo ""
	@echo "  Kernel: $@"
	@ls -lh $@

$(OUT):
	mkdir -p $(OUT)

# ── GRUB ISO ──────────────────────────────────────────────────────────────────
ISO := $(OUT)/shedos-bare.iso

iso: $(KERNEL)
	@command -v $(GRUB_MKRESCUE) >/dev/null 2>&1 || \
		{ echo "$(GRUB_MKRESCUE) not found — brew install x86_64-elf-grub xorriso (Mac) or apt install grub-pc-bin grub-efi-amd64-bin xorriso (Linux)"; exit 1; }
	mkdir -p $(OUT)/isoroot/boot/grub
	cp $(KERNEL) $(OUT)/isoroot/boot/kernel.elf
	cp iso/grub.cfg $(OUT)/isoroot/boot/grub/grub.cfg
	$(GRUB_MKRESCUE) -o $(ISO) $(OUT)/isoroot 2>/dev/null
	@echo "  ISO: $(ISO)"

# ── QEMU launch ───────────────────────────────────────────────────────────────
# -M pc (i440fx, conventional PCI) + disable-modern=on gives a *legacy*
# virtio-net device with an I/O-port BAR0 — what drivers/virtio_net.c drives.
# Legacy virtio is not valid on a q35 PCIe root port.
QEMU_FLAGS := \
    -M pc -m 256M \
    -netdev user,id=n0 \
    -device virtio-net-pci,netdev=n0,disable-modern=on,disable-legacy=off \
    -serial stdio \
    -display none \
    -no-reboot

qemu-kernel: $(KERNEL)
	qemu-system-x86_64 $(QEMU_FLAGS) -kernel $(KERNEL)

qemu: iso
	qemu-system-x86_64 $(QEMU_FLAGS) -cdrom $(ISO)

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -rf $(OUT)
