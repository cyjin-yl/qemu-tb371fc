# Isolated architectural CPU and shared PCI UFS validation.
# Keep this independent of TB371FC board/device selection.
CONFIG_ARM_VIRT=y
CONFIG_ARM_GICV5=y
CONFIG_GENERIC_LOADER=y
CONFIG_UFS_PCI=y
