; entry.asm — Multiboot2 entry point for ShedOS bare-metal kernel
;
; GRUB loads us in 32-bit protected mode with a flat 4 GB segment.
; We set up a minimal GDT, switch to 64-bit long mode, establish a
; stack, and jump to kernel_main() in C.
;
; Identity-maps the first 2 MiB using a single 2 MiB PML4 → PDPT → PD
; huge page.  That covers the kernel image (loaded at 1 MiB) and the
; initial stack.

bits 32

; ── Multiboot2 header ────────────────────────────────────────────────────────
MB2_MAGIC   equ 0xe85250d6
MB2_ARCH    equ 0           ; i386 protected mode
MB2_HDRLEN  equ (mb2_end - mb2_start)
MB2_CHECKSUM equ -(MB2_MAGIC + MB2_ARCH + MB2_HDRLEN)

section .multiboot2 progbits alloc
align 8
mb2_start:
    dd MB2_MAGIC
    dd MB2_ARCH
    dd MB2_HDRLEN
    dd MB2_CHECKSUM
    ; terminator tag
    dw 0    ; type = END
    dw 0    ; flags
    dd 8    ; size
mb2_end:

; ── BSS: page tables + stack ─────────────────────────────────────────────────
section .bss nobits alloc write
align 4096
pml4:  resb 4096
pdpt:  resb 4096
pd:    resb 4096

align 16
stack_bottom:
    resb 65536      ; 64 KiB initial kernel stack
stack_top:

; ── 32-bit bootstrap ─────────────────────────────────────────────────────────
section .text progbits alloc exec
global _start
extern kernel_main

_start:
    cli
    ; Save multiboot2 info pointer (ebx) — pass to kernel_main later
    mov edi, ebx        ; arg1 (info ptr) for kernel_main via SysV ABI

    ; Build identity-map for first 2 MiB
    ; PML4[0] -> pdpt (present, writable)
    mov eax, pdpt
    or  eax, 0x03
    mov [pml4], eax

    ; PDPT[0] -> pd (present, writable)
    mov eax, pd
    or  eax, 0x03
    mov [pdpt], eax

    ; Identity-map the first 1 GiB: PD[0..511], each a 2 MiB huge page.
    ; heap.c/pmm.c assume phys==virt across the first 1 GiB; mapping only
    ; PD[0] (2 MiB) page-faults as soon as the heap reaches 4 MiB.
    ; High dword of each entry stays 0 (GRUB zero-fills .bss).
    mov ecx, 0                  ; PD entry index
    mov eax, 0x00000083         ; phys 0 | PS|RW|P
.fill_pd:
    mov [pd + ecx*8], eax
    add eax, 0x200000           ; next 2 MiB frame
    inc ecx
    cmp ecx, 512
    jne .fill_pd

    ; Load CR3 with PML4 base
    mov eax, pml4
    mov cr3, eax

    ; Enable PAE (CR4.PAE = 1)
    mov eax, cr4
    or  eax, (1 << 5)
    mov cr4, eax

    ; Enable long mode (EFER.LME = 1)
    mov ecx, 0xC0000080     ; MSR_EFER
    rdmsr
    or  eax, (1 << 8)       ; LME bit
    wrmsr

    ; Enable paging + protected mode in CR0
    mov eax, cr0
    or  eax, (1 << 31) | 1
    mov cr0, eax

    ; Load our own GDT before the far jump — GRUB's leftover GDT has no
    ; 64-bit code descriptor at selector 0x08, so jumping without this
    ; #GPs and triple-faults.
    lgdt [gdt64_ptr]

    ; Far jump to flush pipeline and enter long mode with our 64-bit segment
    jmp 0x08:.long_mode

bits 64
.long_mode:
    ; Set up data segments
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax

    ; Set up stack
    mov rsp, stack_top

    ; edi still holds the multiboot2 info pointer (zero-extended to rdi)
    ; kernel_main(uint32_t mb2_info_phys)
    call kernel_main

    ; Should never return — halt if it does
.halt:
    cli
    hlt
    jmp .halt

; ── Minimal 64-bit GDT ───────────────────────────────────────────────────────
section .rodata progbits alloc
align 8
gdt64:
    dq 0                        ; null descriptor
    dq 0x00AF9A000000FFFF       ; code: 64-bit, present, DPL=0, exec/read
    dq 0x00CF92000000FFFF       ; data: 32/64-bit, present, DPL=0, read/write
gdt64_ptr:
    dw ($ - gdt64 - 1)
    dq gdt64
