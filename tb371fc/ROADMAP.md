# TB371FC 整机模拟：实现与验收路线

目标是让原生来宾驱动面对一致的软件可见硬件接口，并最终运行完整 Android 的显示、输入、存储、连接和设备生命周期。
每个完成项需要来源、实现、实际运行回执及明确的实机对照范围。功能测试、驱动 probe 和真实芯片等价是不同门槛；TCG 执行速度不代表 Snapdragon 性能。

以下状态按已归档回执填写。最早的 Linux 八项/GENI 十三项结果保留为历史；当前组件验收索引见 [README](../README.md)，公共 CI 计划见 [ci.md](docs/ci.md)，08:20 隔离构建已通过的 47 项、A77 852 + 2 项和 PCI UFS 15 项见[该轮回执](evidence/verified-20261123-0820/final-verification.json)；独立冻结构建 12 的 [46 项历史回执](evidence/resume-final-20261123/result.json)与后续 49/52/55 项增量及失败记录均见[持续验证账本](evidence/VALIDATION-continued-20261123.md)。CI 中已注册的门槛仍须在对应提交实际执行成功。

## 1. CPU、内存、中断与启动基础

- [x] 独立 `-M tb371fc`，固定 QEMU 11.1.3 上游基线和独立构建环境。
- [x] 实机只读 DT/sysfs 核对型号、CPU affinity、MIDR/REVIDR、RAM、GIC、timer、UART 和外设依赖。
- [x] 新增 Cortex-A77 r1p0 架构模型，替换大核 A76 surrogate；9 种配置合计 852 项 guest 指令/ID/PMU 检查、2 项非法配置拒绝。
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
- [x] 实现显式 `smem-profile=reference-empty`：loader 初始化普通 RAM 的空 SMEM v12 交接格式，要求独立选择 TCSR unlocked-reference；默认 `off` 不初始化。包含普通 reset 恢复空堆、普通 RAM 迁移与双向 profile 不匹配拒绝的协议。
- [x] 提交 `408d82a` 的 [GitHub Actions 运行 45164018326](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45164018326)通过 53 组组件 gate 与四组无补丁 Linux gate；独立 A77/PCI 任务成功，范围限于该次 CI 构建与配置。
- [ ] 收取 doesvm 另一路源码构建的完整本地复验结果；其未归档结果不作为通过记录。
- [x] [408d82a 已验收 CI 二进制的原厂 SMEM 观察](evidence/gdsc-smem-20261123/native-ci-408.json)：未修改生产内核与完整 DT 输入，`qcom_smem_probe` 成功路径、非零 `__smem` 和 23 项运行状态一致性检查；观察器未写 guest 内存，显式参考配置与输入哈希保留。
- [ ] 原厂 SMEM 后续真实分配/消费者操作、完整原生 DT 到达用户态 PID 1 和 Android 启动；接受空参考交接不提供原生 SBL 内容或远端服务。
- [x] 补齐 GCC binding 143 `gcc_sys_noc_cpuss_ahb_clk` 的 `0x48198` halt/status 与 binding 18 父时钟；状态来自实际 clock/vote 请求，源码见 `0a617bab`。
- [x] [0a617bab CI](evidence/gdsc-smem-20261123/ci-0a617ba.json)：GCC 24 项行为与一次真实迁移中的 8 项检查通过；53 组件＋另次四组 Linux、GDSC 29、SMEM 22、独立 A77 852＋2 和 PCI 15 均通过。
- [x] [下载板级产物与原始回执核对](evidence/gdsc-smem-20261123/artifact-0a617ba.json)：`a2e19b18…` 可执行文件与该轮 53/4 两份报告哈希一致；[真实 Python 再生](evidence/gdsc-smem-20261123/gcc-regeneration-0a617ba.json)的两份生成文件逐字节匹配受测提交。
- [x] [0a617bab 原厂完整 DT 诊断](evidence/gdsc-smem-20261123/native-ci-0a.json)：与该轮 CI 一致的 `a2e19b18…` 二进制再次观察到 SMEM 成功路径和 23/23 状态检查，观察器未写 guest 内存；已越过先前 `0x00148198` 的 GCC abort。历史 19/5 与独立 46/47/49/52/55 回执保持原范围。
- [ ] 补齐下一处已观察的 GCC 读取：`mux_get_parent` 在偏移 `0xf060` / 物理地址 `0x0010f060` 触发 unsupported read 和 `ESR 0x96000210`，生产内核 panic。[官方固定版本源码](docs/gcc-sm8250.md#next-observed-boundary-usb3-primary-pipe-source-mux)对应 USB3 primary PIPE source mux；先反汇编核对生产 getter 的 selector 解码，再实现外部父时钟与未知编码行为。源码中的 PHY/test 1 kHz dummy 不是实测时钟，也不提供 PHY 模型。
- [ ] 原厂 SBL/SMEM 内容、AOP/SCM/TrustZone 服务、secure-world 状态和完整 interconnect/电源依赖。
- [ ] 数字电压请求到真实 PMIC/供电行为的完整模型；不把 reference profile 当作 native 资源地址或测量结果。
- [x] KPSS watchdog 的 16 项数字行为检查及实际 pending-bite 迁移。
- [ ] watchdog 与原生整机 suspend/reset/secure 路由的完整联合验收。
- [x] TLMM GPIO/IRQ/专用 UFS_RESET：冻结构建 7 的 25 项 C QTest、15 项检查/四次实际进程迁移通过。
- [x] IMEM SRAM 与 PS_HOLD 数字输出：7 项边界/字段/复位保留检查，包含真实进程迁移。
- [x] **IMEM 映射消除冻结构建 5 的原生 `msm_restart_probe+0xec` external abort；冻结构建 7 推进到 `dcc_probe+0x114`。**
- [ ] IMEM/PS_HOLD 与 PMIC/PON 的完整 reset/poweroff 联合验收；越过 restart probe 不证明整个关机链。
- [x] DCC map2 read-capture 子集、17 项实际事务/负向/迁移检查；原生驱动已完成列表 3/6/7 编程。
- [x] DCC 单字 WRITE、平坦 LOOP、单寄存器 RMW：16 项新增操作与迁移检查，原有 17 项保持通过。
- [ ] DCC CRC/ATB/CTI、嵌套循环、多字 RMW 及原生三列表完整执行。
- [x] PDC 原生四段 pin→SPI 路由、level/edge 转换：18 项 C QTest 与 10 项检查/两次实际迁移通过；VERSION 仅有显式 reference 选项，实机值仍未知。
- [x] IPCC APPS 消息 latch、self-send、实际 SPI 229、mask/clear/clear-on-read 与真实迁移：16 项检查；不伪造缺失远端的回复。
- [x] SPMI v5 显式 reference profile：25 项字节事务、ownership、错误、IRQ 和迁移检查；真实 SPMI → PDC → GIC 连线另有 8 项检查与一次迁移。
- [ ] 实测 SPMI VERSION/APID/ownership、完整 PMIC 身份/寄存器、PON/charger/regulator 和真实电源行为；合成 IRQ fixture 不替代 PMIC 外设。
- [ ] 原生 PMIC、远端固件服务及其余完整 DT 依赖与完整引导验收。
- [x] **IMEM/PS_HOLD 七项 gate 纳入公开 CI，并以计划回归验证 components/all 均强制执行。**

RPMh 默认 `off`，`reference` / `file` 必须显式选择。
SMEM reference 的布局、loader 重叠检查、reset/迁移与 DT 处理见 [smem-reference.md](docs/smem-reference.md)；真实驱动证据要求见 [smem-driver-observer.md](docs/smem-driver-observer.md)。空交接格式不提供 socinfo、远端身份、SMD/GLINK item 或固件回复。
GCC bootstrap 状态、部分 fabric 输入、QUP version 和设备排序延时仍是明确的模型假设。来源和限制见 [GCC](docs/gcc-sm8250.md)、[RPMh](docs/rpmh-sm8250.md)、[TLMM](docs/tlmm.md)、[watchdog](docs/watchdog.md)及 [boot resources](docs/boot-resources.md)。

## 3. DMA、存储、USB 与输入

- [x] apps/GPU SMMUv2 的非安全 AArch64 stage-1、4 KiB 页表、stream/context、权限、fault、ATS、TLB invalidate：23 项 C QTest。
- [x] UFS Sysbus HCI、Qualcomm wrapper/QMP PHY、GCC 依赖和实际 guest-memory DMA/SCSI 后端。
- [x] UFS 56 项控制器/DMA/错误/Hibern8/WriteBooster/无 LUN 检查；原先 48 项及修复前失败继续保留。
- [x] [共享 UFS core/PCI 的冻结构建 12 配套通用构建检查](evidence/compat-final-20261123/ufs-pci/result.json)和 [08:20 独立通用构建检查](evidence/verified-20261123-0820/pci-ufs/result.json)均为 15/15，无跳过；各自二进制与板级验收分开记录。
- [x] 无 LU 的 upstream Linux 完成八项 PID 1 检查及 guest shutdown；普通 console、空 UFS、UFS/SMMU、RPMh 四个 Linux gate 均在 [冻结构建 12](evidence/resume-final-20261123/result.json)复验，后续公共套件分别保留自己的回执。
- [x] 主线 Qualcomm UFS/QMP/GCC/SMMU 驱动运行：19 项 guest 检查，strict translated DMA domain，宿主独立核对写入数据。
- [ ] Qualcomm TCU/TBU、完整 QSMMU v500 扩展、stage 2/nesting、AArch32 与 LPA/LPA2 等未实现翻译模式。
- [x] 16/64 KiB 架构页表模型通过显式 granule profile 实现，b5f4415 CI 的 [26 组真实 QEMU 验收](evidence/smmuv2-granules-20261123/ci-granules.json)及独立 55 组件/4 Linux 套件均通过；[实现与边界](docs/smmuv2-granules.md)。默认仍只广告 4 KiB，未测量原生 SMMU 大页能力。
- [x] SMMUv2 独立迁移验收：18 项双进程恢复／拒绝检查通过，覆盖页表、DMA、fault IRQ 与 reset；[证据](evidence/smmuv2-migration-20261123/publication.json)。UFS 仍关闭，整板迁移未通过。
- [x] VMState v3 绑定 granule profile；[CT102 扩展验收](evidence/smmuv2-granules-20261123/ct102-with-glib.json)28/28、5 次转移，包含实际旧 v2→新 v3 默认 profile 的 ATS/DMA 延续与旧 v2→扩展 profile 的拒绝。两个 CI 二进制摘要固定，未全局安装依赖。
- [ ] UFS ICE、RPMB 认证、原生 LU 启动选择/保护、完整 task management/reset/error 模式及物理链路行为。
- [ ] UFS core 迁移和有未完成存储 I/O 的整板迁移。现有组件迁移均显式关闭 UFS。
- [ ] SDHCI/microSD、DWC3/USB PHY、GENI I²C/SPI 和各连接器生命周期。
- [ ] NT36532 触摸/笔、键盘盖 I²C-HID、attach/detach/wakeup。
- [ ] CTN730 笔充电、MM8013 电量计、charger 与其余 PMIC 器件。

存储测试使用新建合成磁盘。原生 UFS 几何/feature 的只读证据不意味着所有功能已实现。
错误翻译不能回退为成功的 bypass，短 PRDT 不能损坏相邻扇区，未知寄存器不能被全零成功 stub 隐藏。见 [SMMUv2](docs/smmuv2.md)和 [UFS](docs/ufs-sm8250.md)。

## 4. 显示与 Adreno 650

- [x] GPU CX/GX 的 GDSC 数字控制接口：显式 `reference-off`/`reference-on` 映射五个四字节窗口，软件 collapse、硬件/外部请求、retention/wait、clamp/reset 和配置匹配迁移；默认 `none` 不映射，输出未知状态不解释为实机掉电。
- [ ] GPU GDSC 与实际 parent regulator、GPUCC、GMU、retained memory 及完整电源状态转换的联合实现与验收。
- [ ] 需要时建立明确标为辅助输出的 framebuffer，独立于 GPU 验收。
- [ ] SM8250 DPU、双 DSI、DSC、面板初始化和模式切换。
- [ ] A650 GMU/HFI、ringbuffer、命令包、interrupt/fence、错误恢复。
- [ ] GPU 内存访问与 SMMU、同步、cache 可见性的一致契约。
- [ ] 实际命令执行和渲染，并与固定测试图像及允许误差比较。

GDSC 接口与来源见 [gdsc.md](docs/gdsc.md)；它只描述数字请求和状态，不执行 GPU/GMU 或 secure firmware，也不实现物理供电时序。

验收顺序为 driver probe、提交/完成/fault、实际图像。固定成功 ACK、virtio-gpu、主机 GPU 转发或其他 Adreno 世代的身份都不能作为 A650 完成证据。

## 5. 连接、音频与协处理器

- [ ] QCA6390 PCIe/MHI、Wi-Fi/BT 驱动及固件服务，射频环境单独抽象。
- [ ] ADSP/CDSP、remoteproc、GLINK/RPMsg/QRTR 的所需契约。
- [ ] ASoC/SoundWire/TFA9894/WCD938x 音频、路由与时钟。
- [ ] 相机、传感器及剩余板级器件。
- [ ] 挂起恢复、热状态、唤醒和跨设备电源依赖。

## 6. 原厂软件与完整性验收

- [x] 在本地授权范围读取并识别活动槽 boot/kernel/DT/AOP 资产；原始镜像不发布。
- [x] 未修改的生产 Linux 4.19.157 Image，在生成 DT 和测试 initramfs 下完成八项 PID 1 检查及实际 guest shutdown。
- [x] 无串口生产配置采用只读 GDB 完成记录，核对掩码、uname、online、计时和原样 native probe 输出后恢复执行；不是仅凭 exit 0。
- [ ] 生产内核使用完整原生 DT 达到用户态 PID 1。[408d82a](evidence/gdsc-smem-20261123/native-ci-408.json) 已观察 SMEM 成功路径并越过先前 GDSC abort，后续在 `0x00148198` 发生 GCC external abort。[0a617bab](evidence/gdsc-smem-20261123/native-ci-0a.json) 以同一受测 CI 二进制再次通过 23 项 SMEM 状态检查并越过该地址，随后在 GCC 的 `mux_get_parent` 读取 `0x0010f060` 时 panic；完整原生启动仍未通过。
- [ ] 完整 Android userspace、原生存储布局、显示、输入和网络端到端任务。
- [ ] reset、snapshot/migration、错误注入和多次启动的一致性。
- [ ] 完整逐设备覆盖清单、合成服务表、实机差分及未覆盖行为。

[生产内核 GDB 回执](evidence/production-gdb-exact-20261123/result.json)和[冻结构建 12 独立复验](evidence/production-gdb-frozen12-20261123/result.json)分别证明其明示配置下的八项检查及实际 guest shutdown。
[冻结构建 12 原生 DT 诊断](evidence/native-dt-spmi-reference-20261123/result.json)保留 QSEE 写异常；[原生 DT 推进索引](evidence/native-dt-progression-20261123/progress.json)及各轮增量回执保留后续实际故障边界。
[408d82a 原厂回执](evidence/gdsc-smem-20261123/native-ci-408.json)把 SMEM probe 成功与后续 GCC fault 分开记录，使用与公开 53＋4 gate 相同的 `0190794d…` 二进制；输入完整 DT 保留明确的 loader fixup，不是原厂 Android 完整启动。
不能通过替换生产内核、删除需要的设备或放宽断言把未通过的门槛改写成成功。

## 历史结果保留

最早的独立 upstream Linux 6.12.114 在 8 GiB/八核配置下完成正式 GENI console handoff、八项用户态检查和 PSCI system-off；回执为
[linux-smoke.json](evidence/linux-smoke.json)及[串口日志](evidence/linux-smoke.log)。
其当时的 A76、固定时钟和设备子集说明仅属于那个构建。

此前 research kernel 的 256 页早期页表池耗尽仍保留
[失败回执](evidence/linux-research-kernel-failure.json)与[日志](evidence/linux-research-kernel-failure.log)。
[可选 research-kernel patch](patches/linux-6.12-qemu-early-pgtable.patch)没有应用；
clean kernel 成功不验证该 patch，也不解决真实平板的原始 handoff 问题。

### 显示 RSC 后续增量

- [x] 捕获拓扑对应的显示 RSC、四 voter 共享服务、真实 SPI 129 路径与组件迁移：11 + 6 项通过。
- [x] 新二进制公共 49/49 gate 和 RPMh 专项 53 项通过；见 [新增回执](evidence/display-rsc-20261123/publication.json)。
- [ ] 完整原生 DT 启动：该轮失败于 qsee_irq_probe；最新 QSEE/DCC 增量已越过此处，仍未通过完整启动。

### QSEE 与 DCC 后续增量

- [x] QSEE 两组通知锁存和真实 SPI 348/349：12 项行为、6 项真实迁移。
- [x] 同一冻结二进制公共 52/52 gate、专项 51 项通过；[初次失败与修正均保留](evidence/qsee-dcc-20261123/publication.json)。
- [ ] 原厂完整 DT 仍失败：AOP mailbox 共享内存读与 REFGEN 控制位读；SMEM/SBL、原生 PMIC 和未解析 regulator 等依赖仍缺。

### TCSR, AOP SRAM and REFGEN follow-up

- [x] TCSR 32-slot owner/known-state protocol, reset policy and migration: 24 checks.
- [x] AOP 4 KiB message RAM, all-byte storage, boundaries, reset retention and real migration: 10 checks.
- [x] REFGEN CTRL5 software request, explicit state validity, GPIO transitions and migration: 16 checks.
- [x] Same frozen binary: 55/55 public gates, 50 targeted checks.
- [ ] Native full DT: default REFGEN handoff remains unknown; explicit disabled reference advances to GDSC probe and still panics.
- [ ] Evidence-backed SBL/SMEM initialization and remote-processor services; AOP firmware execution and QMP handshake.
- [ ] Original PMIC routing/identity and unresolved RPMh regulator resources; display/GPU and remaining platform power domains.
