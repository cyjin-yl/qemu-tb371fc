# QEMU TB371FC

[![TB371FC public functional gates](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml/badge.svg?branch=tb371fc)](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml)

面向 **Lenovo TB371FC / SM8250（Kona）** 的整机模拟开发分支，基于 QEMU **11.1.3**，提供独立的 `-M tb371fc`。目标是实现真实来宾驱动可使用的软件可见硬件接口，并用平板的只读证据和实际运行的测试逐项核对。

**当前已实现并实际验证 Cortex-A77 架构模型、GENI FIFO、GCC、精确架构计数器与 MMIO timer、SMMUv2 stage 1、UFS 主机/PHY，以及显式启用的 RPMh 数字资源模型。**
独立的 upstream Linux 6.12.114 已完成八核用户态启动和真实 UFS/SMMU 驱动 I/O；未修改的平板生产内核也已在生成的设备树和测试 initramfs 下完成八项用户态检查及真实 guest shutdown。

**完整 Android 启动和整台平板等价仍是未通过的目标。**
生产内核加测试 initramfs 的成功不等于原厂 Android 系统启动。小核仍用 A55 代替 Qualcomm Kryo；显示、Adreno 650、触控、无线、音频、摄像头以及完整电源/固件契约仍有大量实现工作。缓存容量、功耗、射频、电气行为和真实芯片时序不由这些功能测试证明。

## 当前统一验收

[46 项公共输入验收](tb371fc/evidence/resume-final-20261123/result.json)已在冻结构建 12
（QEMU SHA256 `178bace28c84bced9471b824d413feb18e76ddb15212ec32412ad7a7ec32a675`）
**46/46 全部通过**，用时 74.755 秒，运行前后二进制保持一致。
其中包含 42 个组件/校验 gate 和四个 Linux gate；新加入的 SPMI 25 项、
IPCC 16 项、PDC 18 项 C QTest 与 10 项迁移、DCC 17 项均在同一执行文件上通过。
测试使用公开 Linux 输入和合成 fixture，不需要生产 Image、完整原生 DT 或 AOP 镜像。

独立通用构建的最终复验也已通过：[UFS PCI 15/15，全部实际运行](tb371fc/evidence/compat-final-20261123/ufs-pci/result.json)，以及
[A77 九种配置的 852 项 guest 检查和两项非法配置拒绝](tb371fc/evidence/compat-final-20261123/a77/result.json)。
它们共用 SHA256 `c06bb42b52bc47839fbe40eb5a247c04d70b64840411af6efff1497d0a1d16c0` 的通用构建，
与上述 TB371FC 板级构建分别记录；[源码来源核对](tb371fc/evidence/compat-final-20261123/a77/source-provenance.json)绑定最终源码及实际构建输入。

同一冻结构建还通过独立的[生产内核八项验收及实际 guest power-off](tb371fc/evidence/production-gdb-frozen12-20261123/result.json)。
该验收使用生成 DT 和测试 initramfs。完整原生 DT 的[显式 SPMI reference 诊断](tb371fc/evidence/native-dt-spmi-reference-20261123/result.json)
仍在 `qsee_irq_probe+0x140` 失败；完整原生 DT 和 Android 启动尚未通过。

此前的[42 项成功回执](tb371fc/evidence/resume-20261123/result.json)和
[45 项旧二进制失败回执](tb371fc/evidence/public-ci-20261123/attempt-45-old-binary/result.json)
保留各自的实际结果、输入和二进制哈希。持续结果与限制见
[持续验证账本](tb371fc/evidence/VALIDATION-continued-20261123.md)；
[原始验证账本](tb371fc/evidence/VALIDATION.md)及其原始哈希保持不变。

## 已执行的验证

以下是 2026-11-23（北京时间）的**独立冻结构建**回执。每个链接记录自己的二进制和输入哈希；检查数量表示该测试的断言数量，不是整机覆盖百分比，也不能把不同版本的通过记录直接当作一次整机验收。

| 验证 | 实际结果与证据 | 主要范围 |
| --- | --- | --- |
| 平板与启动布局 | [原始板级配置](tb371fc/evidence/android-board.json)、[扩展设备树/硬件清单](tb371fc/evidence/expansion-20261123/android-device-inventory.json)、[13 项运行时布局检查](tb371fc/evidence/boot-layout-20261123/runtime-layout-frozen4.json) | CPU affinity、内存段、GIC、UART、timer、设备依赖；布局检查保留原先重复 memory 节点失败及修复记录。 |
| Cortex-A77 | [最终复验：9 配置、852 项 guest 检查及 2 项非法配置拒绝](tb371fc/evidence/compat-final-20261123/a77/result.json) | 架构寄存器、实际指令、特征开关、缓存 ID 描述和 PMU；不等于缓存时序或 DSU 模拟。 |
| GENI UART | [14 项寄存器/FIFO/IRQ 检查](tb371fc/evidence/expansion-20261123/revalidation-4/geni-qtest.json)、[6 项真实进程迁移](tb371fc/evidence/expansion-20261123/revalidation-4/geni-migration.json) | 包括修复后空闲 TX watermark 驱动下一次 TTY 发送；Linux 多次串口输出另有实际记录。 |
| GCC 与串口接线 | [19 项 GCC、5 项 GCC 迁移、6 项 clock/reset、4 项接线迁移](tb371fc/evidence/expansion-20261123/revalidation-4/summary.json) | 状态、父时钟、分频、门控、复位、GDSC 及未完成 TX/RX 恢复。 |
| CPU/MMIO timer | [精确计数及修前/修后回执](tb371fc/evidence/mmio-timer-live-status-20261123/) | 每虚拟秒准确 19,200,000 tick；独立 icount 参考、10 项 exact/10 项 legacy MMIO、真实 CPU 与 MMIO 迁移、模式不匹配拒绝，以及 16 次 CPU→GIC 线状态检查。 |
| SMMUv2 | [23/23 C QTest](tb371fc/evidence/smmuv2-20261123/qtest-result.json) | RAM 页表遍历、权限、fault、stream/context bank、ATS、TTBR1、无效模式；保留修前反例。 |
| UFS 与 DMA | [56 项控制器/PHY/DMA 检查](tb371fc/evidence/resume-final-20261123/result.json)、[19 项 Linux UFS/SMMU 检查](tb371fc/evidence/ufs-bringup-20261123/linux-strict-iommu-19.json) | 真实 SCSI 块 I/O、strict DMA domain、flush/reopen、错误路径、Hibern8，以及宿主独立核对写入的 32 KiB 数据。 |
| 共享 UFS core / PCI | [最终 15/15，未跳过任何用例](tb371fc/evidence/compat-final-20261123/ufs-pci/result.json) | 独立通用构建、ARM virt/PCI 路径；证明共享代码的兼容性，范围不包含整块 TB371FC。 |
| RPMh reference/file | [22 项 reference/文件 ABI 检查、5 项迁移](tb371fc/evidence/expansion-20261123/revalidation-5/rpmh-summary.json) | TCS 执行、ARC/VRM/BCM 请求、IRQ、sleep/wake 输入、取消及非法请求不完成。reference 数据显式为合成数据。 |
| Watchdog | [16 项数字行为检查](tb371fc/evidence/watchdog-20261123/qtest-16.json)、[真实 pending-bite 迁移](tb371fc/evidence/watchdog-20261123/migration.json) | bark/bite、pet、counter phase、复位动作、IRQ 和错误频率拒绝；频率是软件配置证据。 |
| TLMM | [25 项 C QTest](tb371fc/evidence/tlmm-20261123/qtest-result.json)、[15 项真实迁移检查](tb371fc/evidence/tlmm-20261123/migration-result.json) | 180 GPIO、边沿/电平 IRQ、输出/使能、专用 UFS reset、显式 boot handoff；四次进程迁移。 |
| IMEM / PS_HOLD | [7 项检查，含实际迁移](tb371fc/evidence/boot-resources-20261123/qtest-migration-7.json) | 可写 SRAM、guest-owned handoff 字段、reset retention 和数字 PS_HOLD 输出；没有伪造 PMIC 关机策略。 |
| Linux RPMh provider | [23 项 guest 与 3 项宿主检查](tb371fc/evidence/rpmh-linux-20261123/result.json) | 显式 reference profile；RSC/clk-rpmh 绑定、CCF 19.2 MHz、真实 TCS IRQ、完成的 XO 事务与清除的 pending IRQ、UFS/SMMU 和宿主磁盘核对。 |
| DCC | [17 项真实捕获/错误/迁移检查](tb371fc/evidence/dcc-20261123/result.json) | 实际 SRAM 与源地址空间事务；reference map2 身份，尚无 CRC、ATB、loop、write/RMW 完整执行。 |
| PDC | [18 项 C QTest 与 10 项真实迁移检查](tb371fc/evidence/resume-final-20261123/result.json) | 原生四段 pin→SPI 映射、实际 GIC pending、level/edge 转换和迁移不重放边沿；native VERSION 仍未知，17/9 历史回执保留。 |
| IPCC | [16 项消息/IRQ/迁移检查](tb371fc/evidence/resume-final-20261123/result.json) | APPS self-send、实际 SPI 229/GIC pending、独立 mask/latch、clear-on-read、容量边界及迁移；没有伪造远端固件回复。 |
| SPMI reference | [25 项控制器/负例/IRQ/迁移检查](tb371fc/evidence/resume-final-20261123/result.json) | 显式 v5 reference APID/owner 表，真实有界字节事务、PDC pin 1 接线；测试 IRQ 外设是显式合成 fixture，未声称完整 PMIC。 |
| 生产内核无串口验收 | [冻结构建 12 的 8/8 用户态检查与实际 guest power-off](tb371fc/evidence/production-gdb-frozen12-20261123/result.json) | 未修改的 4.19.157 生产 Image、8 GiB、8 CPU、生成 DT、测试 PID 1；GDB 只读普通 guest RAM 结果后恢复执行。 |

TLMM、IMEM/PS_HOLD、普通 Linux UFS19 和 reference RPMh 联合 Linux23 已在冻结构建 7（QEMU SHA `f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b`）实际通过。新 kernel SHA 为 `c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`。这些组件和生成 DT 的成功仍不证明完整原生 DT 或 Android 启动。最新集成重放、实际二进制和各 gate verdict 见[46 项当前回执](tb371fc/evidence/resume-final-20261123/result.json)；分项与后续证据见[持续验证账本](tb371fc/evidence/VALIDATION-continued-20261123.md)。

### 三种启动结果的区别

- **公开主线 Linux**：固定版本、无补丁的 upstream 输入，正式 `ttyMSM0` console 接管，用户态检查和 UFS/SMMU I/O 均有实测。UFS 的专用 I/O gate 在检查 guest verdict 和磁盘后由宿主 QMP 结束；它不测试 guest shutdown。
- **未修改的生产内核 + 测试 initramfs**：已有八项用户态检查和真正的 QMP `guest-shutdown` 事件。生产配置没有串口 console，验收通过只读 GDB checkpoint 取出完成掩码、uname、在线 CPU、计时和原样探针输出，再继续 guest 关机。见 [guest-acceptance.md](tb371fc/docs/guest-acceptance.md)。
- **生产内核 + 完整原生 DT / Android 系统**：冻结构建 12 在显式 `spmi-profile=reference` 下越过前期 watchdog、IMEM/restart、DCC、IPCC 和 SPMI probe 阻塞，下一记录故障为 `qsee_irq_probe+0x140` 向 `0x0188601c` 写入 `0xf`。见[最新诊断回执](tb371fc/evidence/native-dt-spmi-reference-20261123/result.json)与[较早逐次记录](tb371fc/evidence/native-dt-progression-20261123/progress.json)。SPMI/APID 身份和所有权是明确的参考假设；这没有证明原生 DCC 列表完整执行、完整 DT 到达 PID 1 或 Android userspace 成功。

最早的 [Linux 八项启动记录](tb371fc/evidence/linux-smoke.json)、[GENI 13 项记录](tb371fc/evidence/geni-qtest.json)和 [20 项 profile 对照](tb371fc/evidence/machine-profile-validation.json)保留为历史。它们不描述所有后续设备或当前 CPU 配置。
早期 research kernel 的页表池失败也保留了[回执](tb371fc/evidence/linux-research-kernel-failure.json)和[日志](tb371fc/evidence/linux-research-kernel-failure.log)；[可选修正建议](tb371fc/patches/linux-6.12-qemu-early-pgtable.patch)仍未应用。

## 当前模型与明确边界

| 组件 | 当前契约 |
| --- | --- |
| CPU | QEMU index 0–3 为 A55 surrogate；4–7 为新增 `cortex-a77` r1p0 架构模型，实机大核 MIDR `0x411fd0d0`、REVIDR `3` 有只读证据。MPIDR 为 `0,0x100,…,0x700`。小核 ID 与实机 Kryo 不同；实际 cache 容量/ways/sets 未采得。 |
| RAM / loader | 默认 8 GiB，从 `0x80000000` 起，保留 Android 可见布局的 69 MiB 排除区。大 RAM 的 raw Image 分配基址为 `0xa0000000`，对应通常的 Image text `0xa0080000`；ELF 使用其链接地址。 |
| GIC / PSCI | QEMU GICv3 按实机地址和 affinity 接线；SMC PSCI 支持已验证的 SMP 与 system-off 路径。没有 Qualcomm EL3/TrustZone 或完整 OEM 启动链。 |
| Timer | CPU 和 MMIO 计数器默认用准确有理换算，`floor(ns×19200000/10^9)`；deadline 向上取整。`counter-exact=off` 只用于保留旧量化模式的兼容检查。 |
| GENI / GCC | FIFO 串口接入有状态 GCC 时钟/复位。QUP 2.5 version 寄存器仍明确为合成兼容值；部分 fabric 输入和启动时钟状态仍为公开的 bootstrap 假设。DMA/GSI、流控和真实 baud timing 未实现。 |
| SMMUv2 | apps/GPU 两个架构窗口，非安全 AArch64 stage-1、4 KiB 页表和真实 DMA 翻译；不冒充已实现 Qualcomm TCU/TBU、stage 2 或全部 QSMMU v500 扩展。GPU SMMU 的存在不等于 GPU 已实现。 |
| UFS | Sysbus HCI、Qualcomm wrapper、QMP PHY、GCC 依赖、SMMU DMA 与 SCSI 后端。使用显式测试磁盘。ICE、RPMB 认证、完整原生 LU 启动属性、全部错误/复位模式和物理 PHY/闪存时序仍未完成。 |
| RPMh | `rpmh-profile=off` 为默认值；`reference` 和 `file` 必须显式选择。file 解析外部 cmd-db 并核对内容；未知资源不会自动获得成功 ACK。它不执行 AOP 固件，也不模拟真实 PMIC 电压。 |
| DCC / PDC / IPCC | 分别实现有界 SRAM read-capture、原生 pin→GIC 中断转换和 APPS 消息 latch。各自的参考身份、仲裁和未覆盖模式见设备文档；不提供未实现的协处理器服务。 |
| SPMI / PMIC | 默认 `spmi-profile=off`；`reference` 显式映射五个原生窗口。v5/APID/owner 表为参考输入，只返回有来源的身份字节，未知字段失败；合成 SID15 IRQ fixture 与真实 PMIC 分开。 |
| 迁移 | GENI、GCC、timer、RPMh、watchdog、TLMM、IMEM/PS_HOLD、DCC、PDC、IPCC 和 SPMI reference 有各自的实际迁移回执。**上游 UFS core 当前阻止整板迁移**；组件测试显式使用 `ufs-enabled=off`。SMMU 与其他未验收状态不能由这些回执推定。 |

地址、属性、启动接口、生成 DT 与原生 DT 的差异见 [machine.md](tb371fc/docs/machine.md)。独立设备文档和来源索引见 [SOURCES.md](tb371fc/SOURCES.md)。

## 构建和公开验收

[Containerfile](tb371fc/Containerfile) 提供开发环境。示例不访问真实平板或已有磁盘：

```sh
git clone --branch tb371fc https://github.com/cyjin-yl/qemu-tb371fc.git
cd qemu-tb371fc
docker build -f tb371fc/Containerfile -t qemu-tb371fc-dev .
docker volume create qemu-tb371fc-work
docker run -it --name tb371fc-build --cpus 4 --memory 4g --memory-swap 6g \
  -v "$PWD":/src -v qemu-tb371fc-work:/work qemu-tb371fc-dev bash
```

容器内使用新的输出目录：

```sh
BUILD_DIR=/work/build JOBS=2 sh tb371fc/scripts/build.sh
ninja -C /work/build -j2 \
  tests/qtest/tb371fc-smmuv2-test tests/qtest/tb371fc-tlmm-test \
  tests/qtest/tb371fc-pdc-test

# 依次执行组件、负例和真实迁移；失败时返回非零并保留日志
python3 tb371fc/scripts/ci_device_tests.py \
  --qemu /work/build/qemu-system-aarch64 --build-dir /work/build \
  --suite components --out /work/results/components

# 校验固定官方源码 SHA，独立构建无补丁 Linux
SMOKE_JOBS=2 bash tb371fc/scripts/build_smoke_kernel.sh /work/clean-linux

# 普通/空 UFS 各八项 + UFS/SMMU 十九项 + RPMh 联合二十三项，均为 8 GiB / 八核
python3 tb371fc/scripts/ci_device_tests.py \
  --qemu /work/build/qemu-system-aarch64 --build-dir /work/build \
  --kernel /work/clean-linux/artifacts/Image \
  --suite linux --out /work/results/linux
```

完整命令计划可用 `--list` 查看。每次运行的 `result.json`、stdout/stderr、输入和日志 SHA、生成 DT、guest ELF/initramfs、合成磁盘及相关 fixture 都保存在输出目录。
公共 kernel 输入须有与 Image 哈希匹配的 `build.json`；脚本不静默替换内核。
CI 的精确门槛和范围见 [ci.md](tb371fc/docs/ci.md)。独立 A77 job 额外构建 `a77-test` 配置并复验 852 项 guest 检查。

只运行一个 Linux gate 时：

```sh
python3 tb371fc/scripts/boot_smoke.py \
  --qemu /work/build/qemu-system-aarch64 \
  --kernel /work/clean-linux/artifacts/Image --out /work/linux-smoke

python3 tb371fc/scripts/boot_ufs.py \
  --qemu /work/build/qemu-system-aarch64 \
  --kernel /work/clean-linux/artifacts/Image --out /work/linux-ufs
```

仅有 Linux banner、进程退出 0 或达到超时都不能满足这些 gate；它们检查各自的来宾结果和 I/O 证据。
`boot_smoke.py --arch-probe /path/to/arch-probe` 可以复用已在平板执行过的确切 ELF，回执会记录是否字节一致。
公开 smoke kernel 版本为 `6.12.114-tb371fc-qemu-smoke`；它是验证输入，不是平板日常固件。

[GitHub Actions](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml) 使用公开源码、固定 Linux 输入、reference cmd-db 和合成磁盘。
工作流注册并不代表某个提交已通过；实际状态以对应 run 和上传回执为准。CI 产物依赖构建宿主，不是通用多平台安装包。

## 平板证据与来源

初始 [collector](tb371fc/scripts/collect_board.py) 和后续清单从已授权的 `su` 会话读取 DT、sysfs、内核元数据及限定的软件接口。
[原生 ARM64 探针](tb371fc/tests/arch-probe.S) 在普通 Android 用户态运行，读取架构计数器并输出结果；同一 ELF 的[实机记录](tb371fc/evidence/android-arch-probe.json)用于接口对照。

后续启动诊断另外读取了授权的活动槽启动资产，私下保留原始 Image/AOP 输入；公开仓库只放必要的哈希、解码结构、来源和实验结果。没有将分区内容、凭据或用户数据作为 CI 输入。
采集与测试未刷写或重启真实平板，未为获取寄存器而开放裸 MMIO 权限。软件配置、DT 声明和直接测量在各设备文档中分别标注。

完整依赖和剩余验收见 [ROADMAP.md](tb371fc/ROADMAP.md)。上游基线为
`005c61c0ef6f6ae615283440b4bb7be51b2c6ddd`；各源文件保留自己的 SPDX、版权和上游来源，Linux 派生的表继续使用其原许可证。原始 QEMU 说明保留于 [README.rst](README.rst)。
