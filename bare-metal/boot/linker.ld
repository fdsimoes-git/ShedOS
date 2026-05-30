/* linker.ld — ELF64 linker script for ShedOS bare-metal kernel.
 * Kernel is loaded by GRUB at 1 MiB (0x100000).
 * We keep everything in one flat executable segment. */

ENTRY(_start)

SECTIONS {
    . = 1M;     /* load at 1 MiB */

    .multiboot2 : {
        KEEP(*(.multiboot2))
    }

    .text : {
        *(.text .text.*)
    }

    .rodata : ALIGN(4096) {
        *(.rodata .rodata.*)
    }

    .data : ALIGN(4096) {
        *(.data .data.*)
    }

    .bss : ALIGN(4096) {
        *(COMMON)
        *(.bss .bss.*)
    }

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame)
        *(.note.*)
    }
}
