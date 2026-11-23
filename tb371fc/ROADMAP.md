# TB371FC 整机模拟：实现与验收路线

目标是让原生来宾驱动面对一致的软件可见硬件接口，并最终运行完整 Android 的显示、输入、存储、连接和设备生命周期。
每个完成项需要来源、实现、实际运行回执及明确的实机对照范围。功能测试、驱动 probe 和真实芯片等价是不同门槛；TCG 执行速度不代表 Snapdragon 性能。

以下状态按已归档回执填写。最早的 Linux 八项/GENI 十三项结果保留为历史；当前组件验收索引见 [README](../README.md)，公共 CI 计划见 [ci.md](docs/ci.md)，最新冻结构建 12 的 46/46 集成重放见[回执](evidence/resume-final-20261123/result.json)，历史 42 项成功、45 项旧二进制失败及范围差异见[持续验证账本](evidence/VALIDATION-continued-20261123.md)。CI 中已注册的门槛仍须在对应提交实际执行成功。

## 1. CPU、内存、中断与启动基础

- [x] 独立 `-M tb371fc`，固定 QEMU 11.1.3 上游基线和独立构建环境。
- [x] 实机只读 DT/sysfs 核对型号、CPU affinity、MIDR/REVIDR、RAM、GIC、timer、UART 和外设依赖。
- [x] 新增 Cortex-A77 r1p0 架构模型，替换大核 A76 surrogate；[最终独立通用构建复验](evidence/compat-final-20261123/a77/result.json)通过 9 种配置合计 852 项 guest 指令/ID/PMU 检查、2 项非法配置拒绝。
- [ ] Qualcomm 小核 Kryo 的完整架构差分；当前 CPU0–3 仍用 A55 surrogate。
- [ ] 实测 cache 容量、ways/sets、DSU 与实现寄存器；当前 cache 参数是模型选择，不能由 MIDR 或 DT 推断为已测。
- [x] GICv3、八核 PSCI CPU_ON/SMC system-off 和 8 GiB RAM 的直接 Linux 启动。
- [x] 修正 raw Image 大 RAM 加载基址为 `0xa0000000`，恢复两个 Android 可见 memory 段；13 项运行时布局检查通过。
- [x] 同一 ELF 在平板与 guest 验证 EL0 `CNTFRQ`/计数递增。
- [x] CPU 与 MMIO Generic Timer 精确有理计数：每虚拟秒 19,200,000 tick，deadline 向上取整；独立 icount 和 1,848 个算术边界参考。
- [x] MMIO timer 权限、物理/虚拟 compare、signed TVAL、IRQ、offset、wrap、reset 与真实迁移。
- [x] 用同一个修前失败 ELF 验证 CPU timer 迁移与无迁移控制；修复 exact 模式 live ISTATUS，保留失败回执及 legacy 兼容行为。
- [x] CPU timer 输出进入 GIC PPI 的 16 次线状态检查，覆盖到期后 mask/unmask、disable 和重新编程。
- [ ] 完整 CPU off/on、热插拔之外的真实电源生命周期、suspend/resume 和跨设备低功耗行为。

证据见 [cpu.md](docs/cpu.md)、[mmio-timer.md](docs/mmio-timer.md)及
[boot-layout 回执](evidence/boot-layout-20261123/runtime-layout-frozen4.json)。
精确计数指 QEMU 虚拟时间中的频率契约，不是 cycle-accurate CPU 或真实晶振测量。

## 2. Qualcomm 时钟、资源与固件接口

- [x] GENI FIFO 命令、TX/RX、IRQ、W1C、取消/复位：当前 14 项 QTest、6 项真实进程迁移。
- [x] 修复 idle TX watermark，实际 Linux 正式 TTY 多次输出继续工作。
- [x] 有状态 GCC：上游来源的时钟/分频/门控、reset、GDSC；19 项 QTest、5 项迁移。
- [x] GENI 的实际 GCC clock/reset 接线：6 项行为检查、4 项迁移。
- [x] RPMh RSC/TCS 与 ARC/VRM/BCM 数字请求模型，显式 reference/file 配置；22 项 reference/文件 ABI 检查、5 项迁移。
- [x] 可外部输入 cmd-db，检查边界、别名、aux 数据及内容哈希；未知或不支持资源不获得成功 ACK。
- [x] **显式 reference profile 的 RSC/clk-rpmh provider、CCF 19.2 MHz 和实际 TCS IRQ；连同 UFS/SMMU 的 23 项 guest 检查及 3 项宿主 QOM/寄存器检查通过。**
- [ ] 完整实机 live cmd-db 与动态 VRM 注册的对应关系；AOP 静态部分数据不等于 live RAM。
- [ ] 原厂 AOP/SMEM/SCM/TrustZone 服务、secure-world 状态和完整 interconnect/电源依赖。
- [ ] 数字电压请求到真实 PMIC/供电行为的完整模型；不把 reference profile 当作 native 资源地址或测量结果。
- [x] KPSS watchdog 的 16 项数字行为检查及实际 pending-bite 迁移。
- [ ] watchdog 与原生整机 suspend/reset/secure 路由的完整联合验收。
- [x] TLMM GPIO/IRQ/专用 UFS_RESET：冻结构建 7 的 25 项 C QTest、15 项检查/四次实际进程迁移通过。
- [x] IMEM SRAM 与 PS_HOLD 数字输出：7 项边界/字段/复位保留检查，包含真实进程迁移。
- [x] **IMEM 映射消除冻结构建 5 的原生 `msm_restart_probe+0xec` external abort；冻结构建 7 推进到 `dcc_probe+0x114`。**
- [ ] IMEM/PS_HOLD 与 PMIC/PON 的完整 reset/poweroff 联合验收；越过 restart probe 不证明整个关机链。
- [x] DCC map2 read-capture 子集、17 项实际事务/负向/迁移检查；原生驱动已完成列表 3/6/7 编程。
- [ ] DCC loop、write/RMW、CRC/ATB/CTI 和原生三列表完整执行；探测成功与 SRAM read 子集不替代这些模式。
- [x] PDC 原生四段 pin→SPI 路由、level/edge 转换：18 项 C QTest 与 10 项检查/两次实际迁移通过；VERSION 采用显式 reference，实机值仍未知。
- [x] IPCC APPS 消息 latch、self-send、实际 SPI 229、mask/clear/clear-on-read 和真实迁移：16 项检查通过；不伪造缺失远端的回复。
- [x] SPMI v5 显式 reference profile：25 项有界字节事务、ownership、失败路径、PDC pin 1 IRQ 和迁移检查通过。
- [ ] 实测 SPMI VERSION/APID/ownership、完整 PMIC 身份/寄存器、PON/charger/regulator 和真实电源行为；合成 IRQ fixture 不替代 PMIC 外设。
- [ ] QSEE/syscon/SCM 与其余完整原生 DT 依赖的独立实现和引导验收。
- [x] **IMEM/PS_HOLD 七项 gate 纳入公开 CI，并以计划回归验证 components/all 均强制执行。**

RPMh 默认 `off`，`reference` / `file` 必须显式选择。
GCC bootstrap 状态、部分 fabric 输入、QUP version 和设备排序延时仍是明确的模型假设。来源和限制见 [GCC](docs/gcc-sm8250.md)、[RPMh](docs/rpmh-sm8250.md)、[TLMM](docs/tlmm.md)、[watchdog](docs/watchdog.md)及 [boot resources](docs/boot-resources.md)。

## 3. DMA、存储、USB 与输入

- [x] apps/GPU SMMUv2 的非安全 AArch64 stage-1、4 KiB 页表、stream/context、权限、fault、ATS、TLB invalidate：23 项 C QTest。
- [x] UFS Sysbus HCI、Qualcomm wrapper/QMP PHY、GCC 依赖和实际 guest-memory DMA/SCSI 后端。
- [x] UFS 56 项控制器/DMA/错误/Hibern8/WriteBooster/无 LUN 检查；原先 48 项及修复前失败继续保留。
- [x] [共享 UFS core/PCI 的最终 15/15 兼容性检查](evidence/compat-final-20261123/ufs-pci/result.json)，所有用例实际运行；使用独立通用构建，其二进制与 46 项 TB371FC 板级验收分别记录。
- [x] 无 LU 的 upstream Linux 完成八项 PID 1 检查及 guest shutdown；普通 console、空 UFS、UFS/SMMU、RPMh 四个 Linux gate 均在冻结构建 12 复验。
- [x] 主线 Qualcomm UFS/QMP/GCC/SMMU 驱动运行：19 项 guest 检查，strict translated DMA domain，宿主独立核对写入数据。
- [ ] Qualcomm TCU/TBU、完整 QSMMU v500 扩展、stage 2/nesting、16/64 KiB/AArch32 等未实现翻译模式。
- [x] SMMUv2 独立迁移验收：18 项双进程恢复／拒绝检查通过，覆盖页表、DMA、fault IRQ 与 reset；[证据](evidence/smmuv2-migration-20261123/publication.json)。UFS 仍关闭，整板迁移未通过。
- [ ] UFS ICE、RPMB 认证、原生 LU 启动选择/保护、完整 task management/reset/error 模式及物理链路行为。
- [ ] UFS core 迁移和有未完成存储 I/O 的整板迁移。现有组件迁移均显式关闭 UFS。
- [ ] SDHCI/microSD、DWC3/USB PHY、GENI I²C/SPI 和各连接器生命周期。
- [ ] NT36532 触摸/笔、键盘盖 I²C-HID、attach/detach/wakeup。
- [ ] CTN730 笔充电、MM8013 电量计、charger 与其余 PMIC 器件。

存储测试使用新建合成磁盘。原生 UFS 几何/feature 的只读证据不意味着所有功能已实现。
错误翻译不能回退为成功的 bypass，短 PRDT 不能损坏相邻扇区，未知寄存器不能被全零成功 stub 隐藏。见 [SMMUv2](docs/smmuv2.md)和 [UFS](docs/ufs-sm8250.md)。

## 4. 显示与 Adreno 650

- [ ] 需要时建立明确标为辅助输出的 framebuffer，独立于 GPU 验收。
- [ ] SM8250 DPU、双 DSI、DSC、面板初始化和模式切换。
- [ ] A650 GMU/HFI、ringbuffer、命令包、interrupt/fence、错误恢复。
- [ ] GPU 内存访问与 SMMU、同步、cache 可见性的一致契约。
- [ ] 实际命令执行和渲染，并与固定测试图像及允许误差比较。

验收顺序为 driver probe、提交/完成/fault、实际图像。固定成功 ACK、virtio-gpu、主机 GPU 转发或其他 Adreno 世代的身份都不能作为 A650 完成证据。

## 5. 连接、音频与协处理器

- [ ] QCA6390 PCIe/MHI、Wi-Fi/BT 驱动及固件服务，射频环境单独抽象。
- [ ] ADSP/CDSP、remoteproc、GLINK/RPMsg/QRTR 的所需契约。
- [ ] ASoC/SoundWire/TFA9894/WCD938x 音频、路由与时钟。
- [ ] 相机、传感器及剩余板级器件。
- [ ] 挂起恢复、热状态、唤醒和跨设备电源依赖。

## 6. 原厂软件与完整性验收

- [x] 在本地授权范围读取并识别活动槽 boot/kernel/DT/AOP 资产；原始镜像不发布。
- [x] 未修改的生产 Linux 4.19.157 Image，在生成 DT 和测试 initramfs 下完成八项 PID 1 检查及实际 guest shutdown；冻结构建 12 的独立复验再次通过。
- [x] 无串口生产配置采用只读 GDB 完成记录，核对掩码、uname、online、计时和原样 native probe 输出后恢复执行；不是仅凭 exit 0。
- [ ] 生产内核使用完整原生 DT 达到 PID 1。冻结构建 12 在显式 `spmi-profile=reference` 下越过此前 IPCC/SPMI probe 阻塞，下一记录故障为 `qsee_irq_probe+0x140`（向 `0x0188601c` 写 `0xf`）；完整原生 DT 仍未通过。
- [ ] 完整 Android userspace、原生存储布局、显示、输入和网络端到端任务。
- [ ] reset、snapshot/migration、错误注入和多次启动的一致性。
- [ ] 完整逐设备覆盖清单、合成服务表、实机差分及未覆盖行为。

[冻结构建 12 的生产内核 GDB 回执](evidence/production-gdb-frozen12-20261123/result.json)证明其明示配置下的八项检查和实际关机；
[最新原生 DT 诊断](evidence/native-dt-spmi-reference-20261123/result.json)与[较早推进索引](evidence/native-dt-progression-20261123/progress.json)分别保留实际故障边界。
不能通过替换生产内核、删除需要的设备或放宽断言把未通过的门槛改写成成功。

## 历史结果保留

最早的独立 upstream Linux 6.12.114 在 8 GiB/八核配置下完成正式 GENI console handoff、八项用户态检查和 PSCI system-off；回执为
[linux-smoke.json](evidence/linux-smoke.json)及[串口日志](evidence/linux-smoke.log)。
其当时的 A76、固定时钟和设备子集说明仅属于那个构建。

此前 research kernel 的 256 页早期页表池耗尽仍保留
[失败回执](evidence/linux-research-kernel-failure.json)与[日志](evidence/linux-research-kernel-failure.log)。
[可选 research-kernel patch](patches/linux-6.12-qemu-early-pgtable.patch)没有应用；
clean kernel 成功不验证该 patch，也不解决真实平板的原始 handoff 问题。
