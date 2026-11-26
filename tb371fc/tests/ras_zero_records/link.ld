/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(_start)
PHDRS { image PT_LOAD FLAGS(5); }
SECTIONS {
  . = 0x80100000;
  .text : { *(.text.boot) *(.text*) } :image
  .rodata : { *(.rodata*) } :image
  .data : { *(.data*) } :image
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } :image
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
  ASSERT(. < 0x80200000, "RAS probe overlaps host mailbox")
}
