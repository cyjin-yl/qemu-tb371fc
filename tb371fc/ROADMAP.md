# TB371FC 整机模拟：实现与验收路线

Linux 板级补齐与 UniXNU 路线 A 并行推进，按[跨仓库硬件契约](docs/cross-project-hardware-contract.md)共享来源、状态机和测试向量，各自保留实际运行与物理设备验收。

目标是让原生来宾驱动面对一致的软件可见硬件接口，并最终运行完整 Android 的显示、输入、存储、连接和设备生命周期。
每个完成项需要来源、实现、实际运行回执及明确的实机对照范围。功能测试、驱动 probe 和真实芯片等价是不同门槛；TCG 执行速度不代表 Snapdragon 性能。

以下状态按已归档回执填写。最早的 Linux 八项/GENI 十三项结果保留为历史；当前组件验收索引见 [README](../README.md)，公共 CI 计划见 [ci.md](docs/ci.md)，新隔离构建已通过的 47 项、A77 852 + 2 项和 PCI UFS 15 项见[最终回执](evidence/verified-20261123-0820/final-verification.json)，后续计划与失败记录见[持续验证账本](evidence/VALIDATION-continued-20261123.md)。CI 中已注册的门槛仍须在对应提交实际执行成功。

## 2026-11-23 本次推进

- [x] GPU CX 的 RPMh QOM 类型修复，CX/GX 独立电源域状态、正确父资源接线及实际迁移验收。
- [x] 显式 SMEM 空 v12 reference 交接，以及未改生产内核发布驱动对象的只读观察；本轮完整 DT 诊断中 23/23 字段检查成功。
- [x] AOP reference QMP link/channel 握手、受支持的 QDSS 投票、真实 IPCC/GIC 通知、复位和同版迁移拒绝；独立版本 30 组及 6 成功/2 拒绝迁移已通过。
- [x] 原厂驱动实际建立 AOP reference 通道并消费一个受支持的 QDSS 请求；仍有一个未实现请求被明确拒绝，不等于 AOP firmware 或全部服务实现。
- [x] GCC CPUSS 官方来源补充及严格再生成；原厂完整 DT 异常已从 `0x148198` 推进到 USB PIPE mux `0x10f060`。
- [x] 两个 USB PIPE mux 的 15 项检查、v3 迁移及版本拒绝；统一 `a813` 原厂完整 DT 已越过 mux 读取并输出 Registered GCC clocks。
- [x] 整合后的 `a813` 65 gate 和独立 A77/UFS job；已解包核对精确二进制，并在同一板级二进制上获得 SMEM 23/23 和 AOP reference 交换证据。
- [x] GPUCC 数字 PLL/GMU/branch/reset 模型的 30 项、2 完成/2 拒绝迁移；统一 `df667` 的 62/62 组件 + 4/4 Linux 和独立 A77/UFS 全部通过，同一二进制原厂完整 DT 已完成 GPUCC 注册。
- [x] NPUCC 三窗口数字 PLL/RCG/branch/reset、37 项组件/2 完成/2 拒绝迁移；统一 `d790` 的 63/63 组件 + 4/4 Linux 和独立 A77/UFS 全部通过，同一二进制原厂完整 DT 已完成 NPUCC 注册。
- [x] 统一整合默认分支 `339b9cd` 的 SMMUv2 迁移几何保护与历史证据；`0508` 实际通过 65/65 组件 + 4/4 Linux 和独立 A77/UFS，默认分支保持原位。
- [x] MPM sleep counter：固定 32768 Hz、单个只读 word、精确分数及 VMState 1；17 项检查、2 次完成迁移、1 次损坏流拒绝通过，同一 `0508` 原厂完整 DT 实际输出 MPM timestamp/frequency 并继续启动。
- [x] NoC QoS：47 物理端口 / 188 有效 word；20 项、2 次完成迁移、2 次 profile 拒绝、2 次损坏流拒绝通过。统一 `99ef` 完成 66/66 组件 + 4/4 Linux 和独立 A77/UFS；同一原厂运行只读核验 47 地址及 46 非延迟配置，越过 `0x016e9008`。
- [x] apps QSMMUv500 TCU/10 TBU reference：36 项检查、2 次完成迁移、2 次 profile 拒绝和 2 次损坏流拒绝；统一 `3ef98` 的 67/67 组件 + 4/4 Linux 与独立 A77/UFS 全部通过。同一原厂运行越过 VERSION 异常、注册 TBU 资源客户端，继续到 SCSI/USB；未证明 native DMA、物理电源耦合或固件上下文交接。
- [x] TSENS 有限数字参考：两控制器/32 通道的 [34 项检查、2 次完成迁移、2 次 profile 拒绝及 2 次损坏流拒绝](evidence/wholeboard-20261123-a47/tsens24xx-52492a.json)实际通过；[72 项统一 CI](evidence/wholeboard-20261123-a47/public-ci-52492a.json)与[同二进制完整原生 DT 运行](evidence/wholeboard-20261123-a47/native-52492a.json)已确认各 16670 轮参考采样并越过旧 TSENS fatal。温度/使能/1 ms 为显式假设，两个虚拟温度区 EINVAL、校准和物理热行为仍未通过。
- [x] CPUFreq EPSS：[三域参考模型](docs/cpufreq-hw-reference.md)实际通过 30 项、2/2/2 迁移及拒绝检查；`1fe4e1` 的[74 项统一 CI](evidence/wholeboard-20261123-a47/public-ci-1fe4e1.json)与同二进制原厂复测已完成，printk 明确注册成功。合成表不声称原厂频率或电压。
- [x] L3 devfreq：[请求参考模型](docs/devfreq-qcom-fw-reference.md)已在 `b95f97` 实际通过 20 项及 2/2/2 迁移和拒绝检查，全部 75 项统一验收与[同二进制完整 DT 诊断](evidence/wholeboard-20261123-a47/native-b95f97.json)完成；内核明确注册四个 voter 并初始化父设备。只证明数字请求接口，不代表物理缓存频率或固件执行。
- [x] 配置 NoC SBM 组件：`3b31e8` 的 76 项正式 CI 已通过，八端口参考的 28/2/2/2 检查和五个实际迁移流已核验；47 端口 QoS 不变。
- [x] 配置 NoC SBM 原厂 MMIO 使用：`09c638` 接收 3 次 CLR、最后 mask=4、SET=0；三状态掩码均为零，已越过旧 SBM 写异常。
- [ ] 配置 NoC SBM 真实流量：幂等 CLR 和静止参考状态不证明实际摄像头／显示／Venus 流量排空或电源耦合。
- [x] RPMh WAIT 参考兼容组件：`09c638` 的 77 项正式 CI 已通过，专项 17/1/2/2、五流和不变主 v2 通过独立 ZIP 核验；真实硅片保留位语义仍未知。
- [ ] RPMh WAIT 原厂高位使用证据：本次显式开启，但掩码计数／日志均为零，不能将启动推进归因于兼容开关。
- [x] 完整 DT 用户态夹具构建／暂存：`09c638` 正式 CI 构建 exact-model 变体；同一 ZIP 的八个成员、ELF/CPIO/3120 B ABI 已实际校验。
- [ ] 完整 DT 夹具执行：primary 在 BPS GDSC `0x0ad07004` 出现工作线程 Oops，尚未运行后续 EL0／八项记录／独立 guest shutdown 验收；旧夹具和四个 Linux gate 不改。
- [ ] 六域相机 GDSC：实际 DT／固定驱动合同、独立默认关闭类型、三态父／请求、194 B 身份保护迁移和 29 组候选检查已准备；配套 GCC AHB retention 仅扩充两个源码支持的请求位，待本组合正式 CI 与同二进制原厂复测。
- [ ] 原厂完整 DT 用户态及 Android 启动；不能将 77 项公共成功、控制器注册或 NoC 配置匹配当作整板可用。

本轮精确构建与原厂证据见 [README](../README.md) 和
[本次原厂对照](evidence/wholeboard-20261123-a47/native-09c638.json)；[早期对照](evidence/wholeboard-20261123-a47/native-progression.json)保留原始范围。
原默认分支历史按原字节保留在[独立索引](evidence/DEFAULT-HISTORY-5fd52c1.md)。

## 1. CPU、内存、中断与启动基础

- [x] 独立 `-M tb371fc`，固定 QEMU 11.1.3 上游基线和独立构建环境。
- [x] 实机只读 DT/sysfs 核对型号、CPU affinity、MIDR/REVIDR、RAM、GIC、timer、UART 和外设依赖。
- [x] 新增 Cortex-A77 r1p0 架构模型，替换大核 A76 surrogate；9 种配置合计 852 项 guest 指令/ID/PMU 检查、2 项非法配置拒绝。
- [ ] Qualcomm 小核 Kryo 的完整架构差分；当前 CPU0–3 仍用 A55 surrogate。
- [x] **20:45 UTC 新一轮真机 sysfs 复核：八核均声明 L1 D/I、私有 L2 与共享 `0-7` 的 L3，保留逐核 MIDR、调度容量 313/777/1024 及完整只读采集器。**[原始回执与哈希](evidence/stock-cpu-cache-20261123/source-binding.json)。
- [ ] 实测 cache 容量、ways/sets、line size、DSU 与实现寄存器；新采集的 128 个几何字段均因文件不存在而未知。当前 cache 参数仍是模型选择，不能由已知共享拓扑或 MIDR 推断为已测。
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
- [x] DCC 单字 WRITE、平坦 LOOP、单寄存器 RMW：16 项新增操作与迁移检查，原有 17 项保持通过。
- [ ] DCC CRC/ATB/CTI、嵌套循环、多字 RMW 及原生三列表完整执行。
- [x] PDC 原生四段 pin→SPI 路由、level/edge 转换：18 项 C QTest 与 10 项检查/两次实际迁移通过；VERSION 仅有显式 reference 选项，实机值仍未知。
- [x] [2026-11-24 RPMh/PDC 联合检查点](evidence/sm8250-contract-20261124/README.md)：保留忙碌期间的最新 sleep/wake 电平，迁移 pending PDC/GIC 状态和剩余期限；普通 7 组及包含实际旧 v2 输入的 8 组通过。仍由显式输入驱动，未实现自主整板睡眠协调。
- [x] IPCC APPS 消息 latch、self-send、实际 SPI 229、mask/clear/clear-on-read 与真实迁移：16 项检查；不伪造缺失远端的回复。
- [x] SPMI v5 显式 reference profile：25 项字节事务、ownership、错误、IRQ 和迁移检查；真实 SPMI → PDC → GIC 连线另有 8 项检查与一次迁移。
- [ ] 实测 SPMI VERSION/APID/ownership、完整 PMIC 身份/寄存器、PON/charger/regulator 和真实电源行为；合成 IRQ fixture 不替代 PMIC 外设。
- [ ] 原生 PMIC、远端固件服务及其余完整 DT 依赖与完整引导验收。
- [x] **IMEM/PS_HOLD 七项 gate 纳入公开 CI，并以计划回归验证 components/all 均强制执行。**

RPMh 默认 `off`，`reference` / `file` 必须显式选择。
GCC bootstrap 状态、部分 fabric 输入、QUP version 和设备排序延时仍是明确的模型假设。来源和限制见 [GCC](docs/gcc-sm8250.md)、[RPMh](docs/rpmh-sm8250.md)、[TLMM](docs/tlmm.md)、[watchdog](docs/watchdog.md)及 [boot resources](docs/boot-resources.md)。

## 3. DMA、存储、USB 与输入

- [x] apps/GPU SMMUv2 的非安全 AArch64 stage-1、4 KiB 页表、stream/context、权限、fault、ATS、TLB invalidate：23 项 C QTest。
- [x] UFS Sysbus HCI、Qualcomm wrapper/QMP PHY、GCC 依赖和实际 guest-memory DMA/SCSI 后端。
- [x] UFS 56 项控制器/DMA/错误/Hibern8/WriteBooster/无 LUN 检查；原先 48 项及修复前失败继续保留。
- [x] [UFS ICE 探测与 crypto 拒绝](docs/sm8250-behavior-audit.md)：显式 unsupported 只读窗口，crypto-enable 描述符在 PRDT/SCSI 前以 OCS 9 完成；板级 14 组及共享 PCI/MCQ 17 组通过，不提供实际加密。
- [x] [Auto-Hibern8](docs/ufs-auto-hibern8.md)：AHIT、自动唤醒、UIC/时钟/复位约束及真实进程迁移；18 组、两次迁移、三个拒绝场景通过。
- [x] [真实 Linux UFS runtime PM](evidence/sm8250-contract-20261124/README.md#linux-runtime-pm-extension)：host/LUN/device WLUN 真实 suspended，原 fd 的普通读唤醒，完整 64 MiB 数据核对；冻结实验与参数化公开入口各 40/40，当前 CI 注册为第五个 Linux gate。
- [ ] 整机 system suspend 以及原生平板电源/固件等价验收。
- [x] [可选 ANOC1/ANOC2 TBU reference](docs/tbu-reference-ecats.md)：HALT/ACK、真实 ECATS walker、两个 SID 的合成 DMA 和状态迁移；16 组、一次迁移、六个拒绝场景通过。默认关闭，不能替代完整 QSMMUv500、物理时钟或原生 DMA 拓扑。
- [x] [共享 UFS core/PCI 的冻结构建 12 配套通用构建检查](evidence/compat-final-20261123/ufs-pci/result.json)和 [08:20 独立通用构建检查](evidence/verified-20261123-0820/pci-ufs/result.json)均为 15/15，无跳过；各自二进制与板级验收分开记录。
- [x] 无 LU 的 upstream Linux 完成八项 PID 1 检查及 guest shutdown；普通 console、空 UFS、UFS/SMMU、RPMh 四个 Linux gate 均在 [冻结构建 12](evidence/resume-final-20261123/result.json)复验，后续公共套件分别保留自己的回执。
- [x] 主线 Qualcomm UFS/QMP/GCC/SMMU 驱动运行：19 项 guest 检查，strict translated DMA domain，宿主独立核对写入数据。
- [ ] Qualcomm TCU/TBU、完整 QSMMU v500 扩展、stage 2/nesting、AArch32 与 LPA/LPA2 等未实现翻译模式。
- [x] 16/64 KiB 架构页表模型通过显式 granule profile 实现，b5f4415 CI 的 [26 组真实 QEMU 验收](evidence/smmuv2-granules-20261123/ci-granules.json)及独立 55 组件/4 Linux 套件均通过；[实现与边界](docs/smmuv2-granules.md)。默认仍只广告 4 KiB，未测量原生 SMMU 大页能力。
- [x] SMMUv2 独立迁移验收：18 项双进程恢复／拒绝检查通过，覆盖页表、DMA、fault IRQ 与 reset；[证据](evidence/smmuv2-migration-20261123/publication.json)。该轮显式关闭 UFS，未验收整板迁移。
- [x] VMState v3 绑定 granule profile；[CT102 扩展验收](evidence/smmuv2-granules-20261123/ct102-with-glib.json)28/28、5 次转移，包含实际旧 v2→新 v3 默认 profile 的 ATS/DMA 延续与旧 v2→扩展 profile 的拒绝。两个 CI 二进制摘要固定，未全局安装依赖。
- [ ] 完整 UFS ICE/密钥与 SCM 服务、RPMB 认证、原生 LU 启动选择/保护、完整 task management/reset/error 模式及物理链路行为。
- [x] `qcom-sm8250-ufs` wrapper 的空 legacy 队列检查点：[03cd8843 原始验收](evidence/ufs-migration-20261123/accepted-03cd8843/ufs-migration/result.json)通过 21 组检查、3 次真实进程迁移、7 个拒绝场景，含非恒等 SID `0xe0` DMA 与外部共享磁盘 I/O；[格式与边界](docs/ufs-migration.md)。
- [x] [839ae467 后续验收](evidence/ufs-migration-20261123/verified-839ae467/ufs-migration-result.json)保留同样的 21/3/7 范围；另取 3 份真实 profile 流作不匹配依据，不计为迁移。6 个被拒 receiver 退出后均将 QMP 读至 EOF，再确认无 RESUME；该完整事件审计仅属于后续回执。
- [ ] 通用 `ufs` PCI 与 `ufs-sysbus` 迁移、未完成 SCSI/MCQ 存储请求的序列化，以及完整原生整板迁移；此前关闭 UFS 的组件回执仍保留各自原范围。
- [x] granule core v3、apps companion v1 和 CPUFreq 已在 `1fe4e1` 完成新的 70 组件/4 Linux 统一验收及同二进制原厂复测；此结果有独立源码/产物绑定。
- [x] SMMU 独立迁移已在固定 1fe4e1 二进制通过：原 18 项/4 次尝试、granule 26 项/3 次尝试、companion 36 项/2+2+2，分别记录实际完成与拒绝；不代表 UFS 或整板迁移。
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
- [x] 未修改的生产 Linux 4.19.157 Image，在生成 DT 和测试 initramfs 下完成八项 PID 1 检查及实际 guest shutdown。
- [x] 无串口生产配置采用只读 GDB 完成记录，核对掩码、uname、online、计时和原样 native probe 输出后恢复执行；不是仅凭 exit 0。
- [ ] 生产内核使用完整原生 DT 达到 PID 1。冻结构建 7 越过 watchdog/restart，后续冻结构建 10 越过 DCC probe 并编程列表 3/6/7；后续故障逐次记录于原生 DT 诊断回执，仍未通过完整启动。
- [ ] 完整 Android userspace、原生存储布局、显示、输入和网络端到端任务。
- [ ] reset、snapshot/migration、错误注入和多次启动的一致性。
- [ ] 完整逐设备覆盖清单、合成服务表、实机差分及未覆盖行为。

[生产内核 GDB 回执](evidence/production-gdb-exact-20261123/result.json)证明其明示配置下的八项检查；
[原生 DT 推进索引](evidence/native-dt-progression-20261123/progress.json)保留实际故障边界。
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
- [ ] Native SBL/SMEM handoff and AOP firmware execution. Later explicit SMEM reference and AOP reference QMP handshake are separately validated above; they do not complete this native-firmware milestone.
- [ ] Original PMIC routing/identity and unresolved RPMh regulator resources; display/GPU and remaining platform power domains.

## 只读启动输入检查点（b95f97 之后）

[启动镜像元数据](evidence/wholeboard-20261123-a47/boot-ramdisk-inputs-b95f97.json)
核对既有 boot 文件、固定 ramdisk slice、gzip/CPIO 和一个 AArch64 init ELF。
[fstab 配置核验](evidence/wholeboard-20261123-a47/boot-fstab-and-sbm-symbols-b95f97.json)
识别 system、system_ext、product、vendor、odm 五个首阶段 logical 挂载，
以及 metadata、userdata 和其它配置分区；前三个 logical 项引用 vbmeta_system。
这不是分区镜像存在性、AVB 认证、用户数据读取或 Android 启动回执。
继续使用既有测试 initramfs 验证完整 DT 用户态，Android 文件系统与完整设备链另行验收。
