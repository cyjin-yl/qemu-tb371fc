/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(_start)
SECTIONS
{
    . = 0x80080000;
    .text : { *(.text.boot) *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
