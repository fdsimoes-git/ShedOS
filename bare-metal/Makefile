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

# ── Flags ─────────────────────────────────────────────────────────────────────
CFLAGS := -std=c11 -ffreestanding -fno-stack-protector -fno-pic \
           -mno-red-zone -m64 -Wall -Wextra -O2 \
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
    mm/pmm.c \
    mm/heap.c \
    lib/printf.c \
    lib/string.c \
    claude/chat.c \
    claude/session.c

# TODO Phase 2b-e: add as implemented:
#   drivers/virtio_net.c  net/arp.c  net/ipv4.c  net/udp.c  net/dns.c
#   net/tcp.c  net/tls.c  http/client.c  claude/client.c

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
	@command -v grub-mkrescue >/dev/null 2>&1 || \
		{ echo "grub-mkrescue not found — brew install grub (Mac) or apt install grub-pc-bin grub-efi-amd64-bin (Linux)"; exit 1; }
	mkdir -p $(OUT)/isoroot/boot/grub
	cp $(KERNEL) $(OUT)/isoroot/boot/kernel.elf
	cp iso/grub.cfg $(OUT)/isoroot/boot/grub/grub.cfg
	grub-mkrescue -o $(ISO) $(OUT)/isoroot 2>/dev/null
	@echo "  ISO: $(ISO)"

# ── QEMU launch ───────────────────────────────────────────────────────────────
QEMU_FLAGS := \
    -M q35 -m 256M \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
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
