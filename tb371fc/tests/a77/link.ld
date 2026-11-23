/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(_start)
PHDRS {
    text PT_LOAD FLAGS(5);
    data PT_LOAD FLAGS(6);
}
SECTIONS {
    . = 0x10000;
    .text : { *(.text.boot) *(.text*) } :text
    .rodata : { *(.rodata*) } :text
    . = ALIGN(4096);
    .data : { *(.data*) } :data
    .bss : { *(.bss*) *(COMMON) } :data
    /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
