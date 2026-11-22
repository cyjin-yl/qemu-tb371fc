# QEMU TB371FC

[![TB371FC functional model](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml/badge.svg?branch=tb371fc)](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml)

面向 **Lenovo TB371FC / SM8250** 的 QEMU 开发分支，目标是逐步实现真实平板的软件可见接口，用于内核移植、驱动开发和回归测试。

**已经实际构建并运行：独立的 `-M tb371fc` machine 能启动无补丁的 upstream Linux 6.12.114，8 个 CPU 上线，正式 GENI console 接管后完成用户态检查并经 PSCI 正常关机。**
开发和构建在 doesvm 的独立 Docker 容器中完成；真实 TB371FC 提供只读硬件证据及同一 ARM64 计数器程序的对照结果。

**当前是实验性的板级功能子集，尚未完成整机模拟，不能启动原厂 Android。**
Adreno 650、显示、UFS、Wi-Fi 和真实电源管理尚未实现。大核使用 Cortex-A76 代用模型；性能、功耗和硬件时序不在当前验收范围内。

## 已完成的实测

下面是 **2026-11-23（北京时间）** 在 doesvm 和真实平板上执行的结果；计数代表各测试定义中的检查数量，不表示整机覆盖率。

| 验证 | 结果与证据 | 范围 |
| --- | --- | --- |
| 实机采集 | [原始证据](tb371fc/evidence/android-board.json)及[解码配置](tb371fc/evidence/board-profile.json) | 型号、DT、CPU MIDR、内存范围、UART、GIC、timer、PSCI；不是全部寄存器转储。 |
| 运行中模型与实机配置对照 | [20/20 必需项通过](tb371fc/evidence/machine-profile-validation.json) | 通过 QMP 和实际生成的 DT 核对；3 项已知差异单独保留。 |
| GENI 寄存器行为 | [13/13 通过](tb371fc/evidence/geni-qtest.json) | 真实 QEMU QTest、串口 socket、FIFO、IRQ、W1C、取消、复位、异常命令。 |
| GENI 迁移 | [6/6 通过](tb371fc/evidence/geni-migration.json) | 两个 QEMU 进程实际 QMP 迁移，保留未完成 TX、未读 RX、IRQ 和 RAM 标记；不是运行中 Linux 迁移。 |
| Linux 启动 | [8/8 用户态检查通过](tb371fc/evidence/linux-smoke.json)，[完整串口日志](tb371fc/evidence/linux-smoke.log) | 8 CPU、8 GiB 配置，earlycon 注销后正式 ttyMSM0 输出；procfs/sysfs、定时器/调度、fork/wait、machine 标识、计数器探针与正常关机。 |
| 同一程序的实机对照 | [平板记录](tb371fc/evidence/android-arch-probe.json)及 Linux 回执 | 同一个 SHA-256 的 ELF 在 Android 与 guest 均读取到 19.2 MHz，计数器均推进；循环耗时不是性能基准。 |

证据索引、工具测试结果、输入哈希、已知诊断信息和不能由这些测试推导出的结论，见 [VALIDATION.md](tb371fc/evidence/VALIDATION.md)。
初次使用既有 research kernel 时遇到的页表补丁失败，也保留了[回执](tb371fc/evidence/linux-research-kernel-failure.json)和[日志](tb371fc/evidence/linux-research-kernel-failure.log)。
成功启动使用独立构建的官方源码，没有修改或替换平板上的内核。

## 实现方式与边界

基于官方 QEMU **11.1.3**，upstream commit 为
`005c61c0ef6f6ae615283440b4bb7be51b2c6ddd`。
新增板级初始化位于 [hw/arm/lenovo-tb371fc.c](hw/arm/lenovo-tb371fc.c)，使用 TB371FC 的设备地址布局。

| 组件 | 当前实现 |
| --- | --- |
| CPU | 8 核，QEMU CPU index 0–3 使用 A55，4–7 使用 A76；MPIDR 为 `0x000, 0x100, …, 0x700`。公开上游 CPU MIDR，不伪装为实机 A77。Linux 逻辑 CPU 编号可能与 QEMU index 不同，见 machine 文档。 |
| RAM | 默认 8 GiB，从 `0x80000000` 开始；guest DT 保留实机 Android 可见布局中的 69 MiB 排除区。没有进行全部内存逐页读写测试。 |
| GICv3 | GICD `0x17a00000`、GICR `0x17a60000`；复用 QEMU GICv3。中断容量是模型选择，尚无实机 GICD_TYPER 的逐位对照。 |
| Timer / PSCI | 19.2 MHz 架构计数器、架构 timer、QEMU SMC PSCI。没有 Qualcomm TrustZone、XBL 或 ABL 模型。 |
| GENI UART | `0xa90000` / SPI 357；FIFO TX/RX、命令状态、中断、清除、取消、复位、迁移。DMA/GSI、波特率时序、流控未实现；Linux TTY RX 尚未验证。 |
| QUP / clocks | 为 Linux GENI probe 提供明确标注的合成版本寄存器和固定时钟；GCC、RPMh 和完整 QUP 尚未实现。 |
| 其余硬件 | Adreno/GMU/SMMU、DPU/DSI/DSC、UFS/SDHCI、USB、Wi-Fi/BT、触控/笔/键盘、音频 DSP、摄像头及真实电源管理尚未建模。 |

实机启用的 console 是 **`0xa90000` / SPI 357**。
通用 SM8250 DT 中常见的 `0x988000` / SPI 603 在本次平板捕获中为 disabled。
生成的 DT 仅描述已提供的模型设备并标注替代方案；原厂 DT 依赖的整套硬件还不能运行。
精确布局、CPU 差异、启动接口和诊断说明见 [machine.md](tb371fc/docs/machine.md)。

## 构建并启动

项目提供 [Containerfile](tb371fc/Containerfile)。在可使用 Docker 的 Linux 开发环境中：

```sh
git clone --branch tb371fc https://github.com/cyjin-yl/qemu-tb371fc.git
cd qemu-tb371fc
docker build -f tb371fc/Containerfile -t qemu-tb371fc-dev .
docker volume create qemu-tb371fc-work
docker run -it --name tb371fc-build --cpus 4 --memory 4g --memory-swap 6g \
  -v "$PWD":/src -v qemu-tb371fc-work:/work qemu-tb371fc-dev bash
```

在容器内执行：

```sh
# 专用 aarch64-softmmu 配置，仅构建本板所需模型
BUILD_DIR=/work/build JOBS=4 sh tb371fc/scripts/build.sh

# 下载并验证固定版本的官方源码，独立构建最小 Linux
SMOKE_JOBS=4 bash tb371fc/scripts/build_smoke_kernel.sh /work/clean-linux

# 创建静态 PID 1 + initramfs，真实启动并保存完整验收回执
python3 tb371fc/scripts/boot_smoke.py \
  --qemu /work/build/qemu-system-aarch64 \
  --kernel /work/clean-linux/artifacts/Image \
  --out /work/linux-smoke
```

成功结果保存在 `/work/linux-smoke/result.json` 和 `serial.log`。
测试要求全部用户态检查成功、架构计数器推进和 QEMU 正常退出；仅出现 Linux banner 或超时均不算通过。
`boot_smoke.py` 的内核是显式输入，不会静默替换。
`--arch-probe /path/to/arch-probe` 可复用在实机执行过的确切 ELF；使用不同工具链重建时，回执会如实记录是否与实机 ELF 字节一致。

官方 smoke kernel 的版本为 `6.12.114-tb371fc-qemu-smoke`，配置和构建来源见
[config fragment](tb371fc/configs/linux-smoke.config)、
[实际构建 manifest](tb371fc/evidence/linux-smoke-kernel-build.json)。
它只用于该功能子集的验收，不是面向平板日常使用的固件。

## 运行设备测试

```sh
python3 tb371fc/scripts/test_board_profile.py
python3 tb371fc/scripts/test_validate_machine.py

python3 tb371fc/scripts/validate_machine.py \
  --qemu /work/build/qemu-system-aarch64 \
  --output /work/machine-profile-validation.json \
  --dtb-output /work/tb371fc-generated.dtb

python3 tb371fc/tests/test_geni_uart.py \
  --qemu /work/build/qemu-system-aarch64
python3 tb371fc/tests/test_geni_migration.py \
  --qemu /work/build/qemu-system-aarch64
```

[GitHub Actions](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml)
从源码编译 QEMU、运行解析器和实际设备测试，再构建固定版本的 Linux 并启动八核 guest。
每次运行上传哈希、DT、串口日志、回执和 smoke 输入产物；具体运行状态以 Actions 为准。
CI 中的二进制依赖相应 Linux 构建环境，不是全平台安装包。

## 平板作为参照

硬件采集时间为 **2026-11-23 05:54:30（北京时间）**，原始证据保留字节、散列、cell 解码和未解决项。
[collector](tb371fc/scripts/collect_board.py) 使用 Python 标准库，通过已授权的 `su` 读取限定属性。
另一个只有 2,264 字节的 [ARM64 探针](tb371fc/tests/arch-probe.S) 在普通 Termux 用户态执行，
只读取架构计数器、进行有限整数循环并输出结果。

这些操作未刷机、重启平板、修改驱动、读取原始按键流或访问裸 MMIO。
采集没有收录设备序列号、MAC、账户凭据或原厂分区内容。
操作与采集范围见 [evidence/README.md](tb371fc/evidence/README.md)。

## 后续工作与来源

完整模拟所需的设备依赖和验收门槛见 [ROADMAP.md](tb371fc/ROADMAP.md)；
GENI 的确切行为子集见 [geni-uart.md](tb371fc/docs/geni-uart.md)；
公开资料及代码复用边界见 [SOURCES.md](tb371fc/SOURCES.md)。

新增源文件采用 GPL-2.0-or-later；上游各文件的许可证和版权声明继续适用。
原始 QEMU 通用说明保留于 [README.rst](README.rst)。
本项目不分发原厂分区、设备凭据或无明确再分发许可的固件。
