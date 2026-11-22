# QEMU TB371FC

面向 **Lenovo TB371FC（小新 Pad Pro 12.7 / SM8250、Snapdragon 870）** 的 QEMU 开发分支。
长期目标是按真实设备的软件可见接口模拟整机，帮助内核移植、驱动开发和回归测试。

**当前是实验性的板级功能子集，尚未完成整机模拟，不能启动原厂 Android，
也未实现 Adreno 650、DPU/DSI、UFS、Wi-Fi 或实际电源管理。**
CPU 大核暂用 Cortex-A76 代替 Cortex-A77；所有性能、功耗和硬件时序结论均在当前范围之外。
具体测试回执放在 [`tb371fc/evidence`](tb371fc/evidence/)，未通过测试的能力不会写成完成。

## 实现方式

这是官方 QEMU **11.1.3**（上游 commit
`005c61c0ef6f6ae615283440b4bb7be51b2c6ddd`）的 fork，新增独立的
`-M tb371fc` machine 和 Qualcomm GENI UART 行为模型。
板级初始化位于 [`hw/arm/lenovo-tb371fc.c`](hw/arm/lenovo-tb371fc.c)，
并不继承通用 `virt` 的地址布局。

| 组件 | 当前实现与明确边界 |
| --- | --- |
| CPU | 八核，MPIDR `0x000, 0x100, …, 0x700`；0–3 使用 QEMU A55，4–7 使用 A76 代用模型。未伪造实机 MIDR。 |
| RAM | 默认 8 GiB，物理地址从 `0x80000000` 开始；来宾 DT 保留实采的 69 MiB 空洞。该空洞是 Android 可见内存布局，不能证明物理 DRAM 不存在。 |
| GICv3 | 复用 QEMU 中断模型，映射 GICD `0x17a00000`、GICR `0x17a60000`。中断容量是模型配置，尚无实际 GICD_TYPER 的逐位比对。 |
| Timer / PSCI | 架构计时器 19.2 MHz，QEMU 的 SMC PSCI 服务。未模拟 Qualcomm TrustZone、XBL 或 ABL。 |
| GENI UART | `0xa90000` / SPI 357，FIFO 收发、命令状态、中断、清除、取消、复位和迁移状态定义。DMA/GSI、波特率时序、流控不在范围内。 |
| QUP / clocks | 为 Linux GENI 探测提供明确标注的合成版本寄存器和固定时钟；不是完整 QUP、GCC 或 RPMh 模型。 |
| 其他外设 | 未映射；不通过填零设备或固定成功应答伪装成可用。GENI 自身未实现的寄存器有 `LOG_UNIMP` 日志。 |

实机 Android 当前启用的 console 是 **`0xa90000` / SPI 357**。
SM8250 通用设备树中常见的 `0x988000` / SPI 603 在这次实机采集中为 disabled。
生成的 DT 只描述本模型提供的设备，并显式记录 CPU 和 QUP 的替代方案；它不是原厂 DT。

## 构建

使用支持 QEMU 11.1.3 的 Linux 开发容器。项目提供
[`tb371fc/Containerfile`](tb371fc/Containerfile)；正常 QEMU 构建依赖同样适用。

```sh
git clone --branch tb371fc https://github.com/cyjin-yl/qemu-tb371fc.git
cd qemu-tb371fc
BUILD_DIR=/work/build JOBS=4 sh tb371fc/scripts/build.sh
/work/build/qemu-system-aarch64 -machine help
```

构建脚本使用专用 `tb371fc.mak`，只构建 AArch64 系统模拟及本板所需设备。
源码来自上游；新增文件沿用 GPL-2.0-or-later，原有文件的各自许可证和版权声明保持有效。
完整 QEMU 通用说明见 [原始 README](README.rst)。

## 运行验证

```sh
# 已采集证据的解码、完整性、隐私边界检查
python3 tb371fc/scripts/test_board_profile.py

# 真正启动 QEMU，以 QTest、QMP 和串口 socket 检验 GENI 寄存器行为
python3 tb371fc/tests/test_geni_uart.py --qemu /work/build/qemu-system-aarch64

# 按 --help 指定输出文件，将运行中 QEMU 的 DT 与实机硬件配置比较
python3 tb371fc/scripts/validate_machine.py --help

# 显式提供带 ARM64/GICv3/PSCI/GENI console/initramfs 支持的 Linux Image
python3 tb371fc/scripts/boot_smoke.py \
  --qemu /work/build/qemu-system-aarch64 \
  --kernel /path/to/Image --out /work/linux-smoke
```

Linux smoke 脚本编译静态 ARM64 PID 1 并打包 initramfs，检查八核在线、
计时器/调度器推进、`fork`/`wait`、文件系统和 machine 标识，再要求 PSCI 正常关机。
它记录命令、二进制 SHA-256、串口日志和结果；不会把超时或仅出现 Linux banner 算作通过。
指定的 Image 不会被自动替换成其他内核。

## 平板作为参照

[`tb371fc/evidence/android-board.json`](tb371fc/evidence/android-board.json)
来自项目所有者的 TB371FC，采集时间为 **2026-11-23 05:54:30（北京时间）**。
捕获包含限定的 DT 属性、型号信息和 CPU MIDR；保留原始字节、散列、cell 解码及未解决项。
采集经已授权的 `su` 只读执行，没有刷机、重启、读取原始按键流、修改驱动或访问裸 MMIO。

对应的 [collector](tb371fc/scripts/collect_board.py) 只使用 Python 标准库。
在已有的平板 root shell 中运行并把 stdout 传回开发机即可；不需要安装系统服务。
采集脚本、证据 provenance 及限制见 [证据说明](tb371fc/evidence/README.md)。

配置匹配只证明所选地址、CPU 拓扑和 DT 属性与一次捕获一致；
并不证明完整硬件行为等价。后续的真机行为对照需单独记录输入、预期和结果。

## 后续工作

完整模拟的设备依赖、验证门槛和未完成内容见
[`tb371fc/ROADMAP.md`](tb371fc/ROADMAP.md)。
GENI 的确切行为子集见 [模型说明](tb371fc/docs/geni-uart.md)。
公开研究来源与可复用代码的限制见 [来源说明](tb371fc/SOURCES.md)。

本项目不发布原厂分区、设备凭据、设备唯一标识或无明确再分发许可的固件。
如需评估自己的原厂内核，请先补齐其实际依赖并保留启动失败日志，
不要将本项目的裁剪 DT 验收结果写成原厂内核兼容性证明。
