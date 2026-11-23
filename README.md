# QEMU TB371FC

[![TB371FC public functional gates](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml/badge.svg?branch=tb371fc-verified-wholeboard-20261123-a47)](https://github.com/cyjin-yl/qemu-tb371fc/actions/workflows/tb371fc.yml)

面向 **Lenovo TB371FC / SM8250（Kona）** 的整机模拟开发分支，基于 QEMU **11.1.3**，提供独立的 `-M tb371fc`。目标是实现真实来宾驱动可使用的软件可见硬件接口，并用平板的只读证据和实际运行的测试逐项核对。

**当前已实现并实际验证 Cortex-A77 架构模型、GENI FIFO、GCC、精确架构计数器与 MMIO timer、SMMUv2 stage 1、UFS 主机/PHY，以及显式启用的 RPMh 数字资源模型。**
独立的 upstream Linux 6.12.114 已完成八核用户态启动和真实 UFS/SMMU 驱动 I/O；未修改的平板生产内核也已在生成的设备树和测试 initramfs 下完成八项用户态检查及真实 guest shutdown。

**完整 Android 启动和整台平板等价仍是未通过的目标。**
生产内核加测试 initramfs 的成功不等于原厂 Android 系统启动。小核仍用 A55 代替 Qualcomm Kryo；显示、Adreno 650、触控、无线、音频、摄像头以及完整电源/固件契约仍有大量实现工作。缓存容量、功耗、射频、电气行为和真实芯片时序不由这些功能测试证明。


## 最新验收：77 项通过，原厂推进到 BPS GDSC（2026-11-23）

统一源码 `09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e` 已实际通过 **77 个 gate：73/73 组件 + 4/4 Linux，以及独立 A77 852+2、PCI UFS 15 项**。见[正式 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45264653096)、[精确产物回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-09c638.json)和[完整日志独审](tb371fc/evidence/wholeboard-20261123-a47/official-ci-log-audit-09c638.json)。板级 QEMU SHA-256 为 `7211873740226d0509e354cfbf1c23bb4eb0459b6eca470187ea3dfd3a2e04ca`；产物保护实际核验 16 份组件报告和 **37 个迁移文件**。完整审计按原字节压缩保留，[可读索引](tb371fc/evidence/wholeboard-20261123-a47/ci-artifact-audit-index-09c638.json)明确标为派生摘要；37 个文件包含负例和损坏副本，不是 37 次成功迁移。

[默认关闭的 WAIT 参考兼容](tb371fc/docs/rpmh-wait-reserved-reference.md)及其[原始报告](tb371fc/evidence/wholeboard-20261123-a47/rpmh-wait-09c638.json)实际通过 **17 项、1 次完成迁移、2 次策略不匹配拒绝和 2 次单 bit 损坏流拒绝**。主 VMState v2 和四项旧 RPMh gate 保留；五个实际流及完整 19420 B 主段／24 B 可选子节已直接核验。兼容只作用于空闲 WAIT 高 16 位，忙碌和其他命令仍严格；这不证明实测硅片的保留位行为。

同一二进制的[未修改生产 Image／完整 DT 诊断](tb371fc/evidence/wholeboard-20261123-a47/native-09c638.json)运行 32.12 秒后按界限停止，SMEM 23/23、AOP reference、CPUFreq 及四个 L3 voter／父设备注册均有实际证据。WAIT 策略虽开启，但掩码计数和对应日志均为 **0**，不能把推进归因于该开关。SBM 实际接收 **3 次 CLR、0 次 SET，最后 CLR mask=4**，停机请求、SENSE、pending 均为零；证明接口被使用，不证明真实流量排空或非平凡延时握手。

新的唯一无效访问是 **读取 BPS GDSC `0x0ad07004` 的 4 B**，在 4.106479 秒触发 CPU7、PID60 `kworker/7:1` 的同步外部异常；调用链包含 `gdsc_probe+0x770`。这是延迟 probe 工作线程 Oops，完整日志没有 PID1 panic，随后 10/20 秒仍有内核日志。诊断程序退出 0 仅表示取证完成。**完整原厂 DT 下的用户态及 guest shutdown 验收、原厂 Android 和整板可用仍未通过。**

完整 DT 专用八项夹具已在本次 CI **实际构建并从同一 ZIP 校验、暂存**：[原始构建清单](tb371fc/evidence/wholeboard-20261123-a47/actual-dt-fixture-manifest-09c638.json)、[暂存审计](tb371fc/evidence/wholeboard-20261123-a47/full-dtb-fixture-stage-audit-09c638.json)。其 ELF/3120 B 结果 ABI、精确 `Qualcomm Technologies, Inc. kona QRD` 名称和 CPIO 已检查，但**没有运行该夹具**；primary 日志中的异常阻止后续 EL0／八项结果／独立关机验收。构建步骤不增加第五个 Linux gate，也不改旧八项夹具。

## 保留的 76 项通过与 RPMh WAIT 异常（2026-11-23）

新增独立的 [NoC SBM 参考握手模型](tb371fc/docs/noc-sbm-reference.md)，按[固定驱动源码和完整实机 DT](tb371fc/evidence/wholeboard-20261123-a47/noc-sbm-source-contract.json)仅映射三个 32 位 word、八个端口。默认保持三个地址未映射；显式启用后区分请求、反馈和挂起状态，支持独立期限、外部参考阻塞、复位和 152 B 的 VMState v1。1000 ns 延迟与冷态连接均为明示参考策略；没有实现摄像头、显示或 Venus 的实际流量排空。

统一源码 `3b31e83dc412962826ada872e361a1d32ea4e463` 已实际通过 **76 个 gate：72/72 组件 + 4/4 Linux，以及独立 A77 852+2、PCI UFS 15 项**。见[正式 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45255339714)和[精确产物回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-3b31e8.json)。板级 QEMU SHA-256 为 `e0e1a669fefee1b20e8421dc6943faabee7fccb91c35f72ae2cd2f45513d23f2`；实际 ZIP 中 15 份组件报告、32 个迁移文件已直接核验。SBM 的[原始报告](tb371fc/evidence/wholeboard-20261123-a47/noc-sbm-3b31e8.json)通过 28 项、2 次完成迁移、2 次 profile 拒绝和 2 次损坏流拒绝，五个实际 152 B payload 与真实日志均已检查。

同一二进制的[未修改原厂 Image／完整 DT 诊断](tb371fc/evidence/wholeboard-20261123-a47/native-3b31e8.json)这次在更早处终止：DRV2 TCS0 `WAIT_FOR_CMPL`（偏移 `0xd10`）写入 `0x778bd800` 被现有模型拒绝，`__tcs_buffer_write+0x220` 触发 PID1 panic。SBM 只读观察虽完整匹配参考几何和冷态，SET/CLR 计数均为零，**未证明原厂越过先前 SBM 异常**。本次未到达 CPUFreq/L3 注册；下面 b95f97 的较远进展仍独立保留。SMEM 23/23、GCC/GPUCC/NPUCC 和 MPM 32768 Hz 是本次实际观察。

[固定原厂机器码取证](tb371fc/evidence/wholeboard-20261123-a47/rpmh-production-disassembly-3b31e8.json)保留五个函数的 4584 B 代码、反汇编及故障指令匹配。[源码与机器码复核](tb371fc/evidence/wholeboard-20261123-a47/rpmh-source-production-review-3b31e8.json)确认生产驱动确实留下未初始化的命令 wait 字段，并把整个 u32 合入 WAIT。保留高位的处理仍需明确模型合同；没有修改原厂 Image，也没有认定真实硅片会产生同样总线错误。[完整 DT 根 model](tb371fc/evidence/wholeboard-20261123-a47/actual-dt-model-3b31e8.json)实际是 `Qualcomm Technologies, Inc. kona QRD`，因此旧用户态夹具的 `TB371FC` 名称判定不能复用为完整 DT 验收。本次没有执行该旧夹具。

[既有启动镜像核验](tb371fc/evidence/wholeboard-20261123-a47/boot-ramdisk-inputs-b95f97.json)确认固定 ramdisk 中存在 AArch64 `init` ELF；[实际挂载配置与生产符号核验](tb371fc/evidence/wholeboard-20261123-a47/boot-fstab-and-sbm-symbols-b95f97.json)确认五个首阶段逻辑分区 `system/system_ext/product/vendor/odm` 及 metadata/data 配置。它们只说明输入内容和配置要求，未执行 init、认证 AVB、读取配置指向的分区或确认完整 Android 镜像已齐。

## 保留的 75 项通过与原厂 L3 注册（2026-11-23）

统一源码 `b95f973f9b20d53e3a0b8843c919156e3acb58d9` 已实际通过 **75 个 gate：71/71 组件 + 4/4 Linux，以及独立 A77/UFS job**。见[正式 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45247295321)和[源码／产物绑定回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-b95f97.json)。板级 QEMU SHA-256 为 `0f6ebed05f414484f1ef04196457c314bbd635245683c547c40d0a601065c88e`；已直接核验下载的 ZIP、两份 clean-source 汇总、14 份组件报告和 **27 个实际迁移文件**。

[L3 Devfreq 原始报告](tb371fc/evidence/wholeboard-20261123-a47/devfreq-qcom-fw-b95f97.json)实际通过 **20 项检查、2 次完成迁移、2 次 profile 拒绝和 2 次单 bit 损坏流拒绝**。独立核验器读取五个完整 72 B payload、实际 QMP/QTest 和接收端错误日志；第三个 unknown 源文件只用于反向拒绝，不作为第三次成功往返。模型只提供三个 DT 窗口内的 168 B 接口和合成名义请求表，不把这些值当作物理缓存频率。

同一个二进制的[未修改生产 Image／完整实机 DT 运行](tb371fc/evidence/wholeboard-20261123-a47/native-b95f97.json)已越过原 `0x18590000` 异常：内核明确注册四个 Devfreq voter 并完成父设备初始化；CPUFreq、SMEM 23/23、AOP QDSS 和此前控制器进展保留。新的唯一无效访问是 **`msm_bus_noc_sbm_config+0x84` 向 `0x01506080` 写入 4 B**。该异常发生在 PID58 的延迟 probe 工作线程，后续内核日志仍继续；不能写成 PID1 panic。完整原厂 DT 用户态、原厂 Android、原厂 DMA、GPU/NPU 执行与整板迁移仍未通过。

## 上一轮 CPUFreq 与 74 项统一验收（2026-11-23）

统一提交 `1fe4e1a8d3e4d1f1d64e800aa10bd355a007eac5` 已实际通过 **74 个 gate：70/70 组件检查 + 4/4 Linux 启动，以及独立 A77/UFS job**。见[正式 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45240510512)和[冻结回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-1fe4e1.json)。板级 QEMU SHA-256 为 `4edcf24e59963b2e33942c042cdd04f8a96c2d6faf6891dfd372b8eb219f5b95`；已直接读取 ZIP、包内二进制、两份 clean-source 汇总、13 份组件报告及 **22 个实际迁移文件**。三组 CI unit 实际为 29、17、14 项。

[CPUFreq EPSS 的原始回执](tb371fc/evidence/wholeboard-20261123-a47/cpufreq-hw-1fe4e1.json)通过 **30 项检查、2 次完成迁移、2 次 profile 拒绝和 2 次损坏流拒绝**。独立核验实际五个迁移文件的十五个域 payload、保存前后的真实 QMP、目标不同时间起点与首个纳秒续进。合入的 SMMUv2 granule 门禁[实际通过 26 项](tb371fc/evidence/wholeboard-20261123-a47/smmuv2-granules-1fe4e1.json)，默认 companion 的 36 项和 core v3 实际流检查同时通过。

同一个已验收二进制的[生产 Image／完整实机 DT 运行](tb371fc/evidence/wholeboard-20261123-a47/native-1fe4e1.json)已越过旧 `0x18591100` 异常，内核明确报告 **`cpufreq: driver qcom-cpufreq-hw up and running`**。停止状态下三域均与显式参考表一致；SMEM 23/23、NoC 46/46 非延迟配置、两组 TSENS 采样和 SMMUv500 资源状态保留。新的实际 fatal 是 **`devfreq_qcom_fw_driver_probe+0x94` 读取 `0x18590000`**，对应[实际 DT 的 L3 devfreq en-base](tb371fc/evidence/wholeboard-20261123-a47/devfreq-dt-symbols-1fe4e1.json)。完整实机 DT 用户态、原厂 Android、GPU/NPU 执行和整板等价仍未通过。

首次 74 项组合 `530909a` 的[正式构建失败](tb371fc/evidence/wholeboard-20261123-a47/public-ci-530909-build-failure.json)已保留：新增 header 的 `first_cpu` 成员与 QEMU 已有宏冲突，板级编译失败，70 项组件和 4 项 Linux 均未执行。失败 ZIP 仅有构建日志，安装保护在缺少二进制时退出，没有启动原厂复测。独立 CPU job 的 852 项来宾检查、2 项配置拒绝和 15 项 UFS 回归通过。最小修复 `4dbcb3e` 仅将该成员改名为 `first_cpu_index`，保持类型、布局、模型行为和全部门槛不变；修复后的 `1fe4e1` 组合已完成上述正式重跑。

统一提交 `52492a797d2acd9c918a8639564e8ff3e7beab3f` 已实际通过 **72 个 gate：68/68 组件检查 + 4/4 Linux 启动，以及独立 A77/UFS job**。见[实际 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45226846782)和[冻结回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-52492a.json)。板级 QEMU SHA-256 为 `debe05589d1673680231dada5377c68b0f7f7ec1b2432f8fb7eff249e8a582e3`；已直接读取 ZIP、包内二进制、两份 clean-source 汇总、11 份组件报告及 **17 个 MPM/NoC/SMMUv500/TSENS 迁移文件**。

新增[两组 TSENS24xx 数字参考模型](tb371fc/docs/tsens24xx-reference.md)的[原始回执](tb371fc/evidence/wholeboard-20261123-a47/tsens24xx-52492a.json)实际通过 **34 项检查、2 次完成迁移、2 次 profile 拒绝和 2 次损坏流拒绝**。独立解码器检查五个真实迁移文件及六个完整控制器 payload，并将可变字段与停止状态下的真实 QOM 观测比对。默认仍为未知；`0xffff` 初始使能、25°C 输入与 1 ms 采样均是显式参考假设。

同一个通过 CI 的二进制运行[未修改的生产 Image 与完整原生 DT](tb371fc/evidence/wholeboard-20261123-a47/native-52492a.json)，已越过 `0x0c222004` 的 TSENS 异常：两个控制器各完成 **16,670 轮采样**，32 个建模通道的 VALID、代码与温度换算一致，TRDY 均为 8。实际 DT 引用其中 25 个独立通道；这不是实测芯片温度，也没有把两个仍报 EINVAL 的虚拟温度区算作通过。SMEM 23/23、AOP QDSS、NoC 非延迟配置及 SMMUv500 资源注册均保留。新的实际 fatal 为 **`qcom_cpufreq_hw_driver_probe+0x408` 读取物理地址 `0x18591100`**。完整原生 DT 用户态、原厂 Android、GPU/NPU 执行及整板等价仍未通过。

首次 TSENS 组合 `298f21c` 的[失败回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-298f21-failure.json)保留为 **67/68 组件通过、Linux 四项未执行**；其失败产物没有用于原厂启动。TSENS 前 17 项通过，第 18 项误把非法宽度诊断期望为 `rejected`。只修正为精确的 `invalid size (min:4 max:4)` 后，设备 C、34 项门槛和迁移要求均不变，上述 52492a 完整重跑通过。[原始失败报告](tb371fc/evidence/wholeboard-20261123-a47/tsens24xx-298f21-failed.json)和真实 QTest/错误日志按原字节保留。

以下是各自绑定源码和二进制的历史检查点。

统一提交 `3ef98a8afb49a873d677288693d6491c50fd75e1` 已实际通过 **71 个 gate：67/67 组件检查 + 4/4 Linux 启动，以及独立 A77/UFS job**。见[实际 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45216035665)和[冻结回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-3ef98.json)。板级 QEMU SHA-256 为 `703af9a899455c1c32636a9e3535294031c93cd9624fdc193177c61cb8ff7600`；已实际读取 ZIP、包内二进制、两份 clean-source 汇总、十份组件报告及 12 个 MPM/NoC/SMMUv500 迁移文件。

[apps SMMUv500 companion](tb371fc/docs/smmuv500-reference.md)的 [36 项实际检查](tb371fc/evidence/wholeboard-20261123-a47/smmuv500-3ef98.json)、2 次完成迁移、2 次 profile 拒绝和 2 次损坏流拒绝全部通过。它实现 TCU 与 10 个 TBU 的有限 reference 协议，包含有状态 HALT/ECATS、共享 SMMUv2 页表与故障路径、真实 DMA 缓存失效和迁移；`0x02000000` 身份及 1 µs 延时均是明示参考假设。

同一二进制的[原厂完整 DT 运行](tb371fc/evidence/wholeboard-20261123-a47/native-3ef98.json)已越过 TCU VERSION 异常：4 个 MNOC TBU 连接 regulator，父 apps SMMU 与随后十个资源客户端注册，启动继续到 SCSI/USB。停机观察的 10/10 TBU 几何匹配实际 DT；所有请求保持冷态，**没有把它算作原厂 DMA 或 ECATS 验证**。SMEM 23/23、AOP QDSS、MPM 32768 Hz 和 NoC 46/46 非延迟配置仍保留。下一实际 fatal 是 **`__tsens2xxx_hw_init+0x10` 读取 `0x0c222004`**；[实际 TSENS DT](tb371fc/evidence/wholeboard-20261123-a47/tsens-dt-evidence-3ef98.json)声明两组 24xx 控制器。完整原生 DT 用户态、原厂 Android 和整板等价仍未通过。

统一提交 `99ef6052e3e10e84c67f3482471f4bde12ab295a` 已实际通过 **70 个 gate：66/66 组件检查 + 4/4 Linux 启动，以及独立 A77/UFS job**。见[实际 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45206934707)和[冻结回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-99ef.json)。板级二进制 SHA-256 为 `226f826f02a97978f9f0afbcc1241dbcd6c25faca4840f8e1f72cee22f3d0000`；ZIP、包内 QEMU、两份 clean-source 汇总、九份组件报告及七个 MPM/NoC 迁移文件均已实际读取核验。

[NoC QoS 配置 reference](tb371fc/docs/noc-qos-reference.md)按实际 DT 的 47 个独立端口仅映射 188 个 32 位 word，默认拒绝访问；显式启用后保存合法整字配置并绑定迁移身份。其[原始测试报告](tb371fc/evidence/wholeboard-20261123-a47/noc-qos-99ef.json)实际通过 **20 项检查、2 次完成迁移、2 次 profile 拒绝和 2 次损坏流拒绝**。原厂运行时没有预置 DT 优先级、伪造 SBM 确认或带宽结果。

同一个 CI 二进制的[生产 Image / 完整原生 DT 运行](tb371fc/evidence/wholeboard-20261123-a47/native-99ef.json)已越过 NoC 首异常：停机只读观察 **47/47 物理地址正确，46/46 非延迟端口的四字配置与 DT/源码预期一致**；IPA 延迟端口保持零。GCC、GPUCC、NPUCC 注册，MPM 读取、SMEM **23/23** 及 AOP QDSS 交换仍保留。下一实际 fatal 是 **`qsmmuv500_arch_init+0x88` 读取物理地址 `0x15182018`**，QEMU 日志直接确认该地址。它对应[实际 DT](tb371fc/evidence/wholeboard-20261123-a47/smmuv500-dt-evidence-99ef.json)的 apps TCU `0x15182000 + 0x18` VERSION；[后续接口研究](tb371fc/docs/smmuv500-source-contract.md)记录 TCU/TBU、ECATS 和未测版本/时序边界。KGSL SMMU 更早因 `gcc_gpu_memnoc_gfx_clk` 未就绪而返回 -16；该警告没有被强行消除。**完整原生 DT 用户态、原厂 Android 和整板等价仍未通过。**

统一提交 `0508fe9d5382b3e94ee0664012ff1e6f0457e5b7` 已实际通过 **69 个 gate：65/65 组件检查 + 4/4 Linux 启动，以及独立 A77/UFS job**。见[本次 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45198993920)及[冻结回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-0508.json)。板级二进制 SHA-256 为 `f3582740b3ccbe3689181f796412d8d28cdfc92ad0ea48e639334dd464c6548b`；已下载 ZIP、直接核对包内文件，并验证两套测试的源码干净状态和前后哈希一致。

本轮加入独立的 **32768 Hz MPM sleep counter**，实际完成 **17 项检查、2 次进程间迁移和 1 次损坏流拒绝**；[原始报告](tb371fc/evidence/wholeboard-20261123-a47/mpm2-0508.json)保持 ZIP 内字节，三个迁移文件也已逐个核对哈希。默认分支 `339b9cd` 的 SMMUv2 几何保护同轮完成 **18 项检查、2 次完成迁移和 2 次容量不匹配拒绝**。两份原分支历史和旧账本均按原字节保留，默认分支没有移动。

前一 `d79052f306ec37a797d05c9e4b2f8f7ea69c0675` 的 [67 项成功](tb371fc/evidence/wholeboard-20261123-a47/public-ci-d790.json)仍绑定其自己的源码与二进制。

NPUCC 增量实现三个物理窗口中的 3 组 PLL、6 个 RCG、33 路 branch、7 路 reset，以及真实 XO/GCC Clock 接线；[契约与边界](tb371fc/docs/npucc-sm8250.md)。其 **37 项检查、2 次完成迁移和 2 次配置拒绝**已在上述统一构建实际通过。GPUCC 的 **30 项检查、2 次完成迁移和 2 次配置拒绝**也在同一二进制通过；[GPUCC 契约](tb371fc/docs/gpucc-sm8250.md)。

同一个通过公开 CI 的二进制运行未修改的生产内核和完整原生 DT，已实际输出 **Registered GCC clocks、Registered GPU CC clocks、Registered NPU CC clocks**，并越过此前的 `boot_stats_init` 计数器异常，输出 **MPM timestamp = 112563 / frequency = 32768**。SMEM 为 **23/23**，AOP reference 通道已消费 QDSS 请求。[原厂运行回执](tb371fc/evidence/wholeboard-20261123-a47/native-0508.json)的下一实际 fatal 是 **`msm_bus_noc_qos_init+0x48` 对物理地址 `0x016e9008` 的读取**；QEMU 的 `invalid_mem` 与同一 CPU 的异常日志直接确认该地址。实际 Image 内嵌配置选择 RPMh NoC 驱动，完整 DT 对应 Aggre1 port 7 的 QoS MAINCTL。当时从[限定 DT 子树](tb371fc/evidence/wholeboard-20261123-a47/noc-dt-evidence-0508.json)整理出 [47 个独立端口 / 188 个有效寄存器](tb371fc/evidence/wholeboard-20261123-a47/noc-layout-0508.json)；该故障现由上面的 99ef 增量越过，0508 回执保留原始结论。VIDEOCC 仍因总线驱动未就绪而延后探测，没有到达其 PLL MMIO。

SMEM 后续 minidump/PIL 警告与其 602 项编号超出 reference 的 512 项边界相符；SoC 信息的 137 项编号合法，但空参考分区没有对应固件记录，因此回退到 dummy 数据。已核对官方源码和实际警告位置；没有改写这些记录，也没有把它们解释为先前 SMEM 发布验证失败。

前一统一 `df667a3e8dbb31368ebbd6af1a7569a2b4c785d0` 已实际通过 **66 个 gate：62/62 组件 + 4/4 Linux，以及独立 A77/UFS**；其[公开回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-df667.json)与[原厂 NPUCC 阻塞](tb371fc/evidence/wholeboard-20261123-a47/native-df667.json)按原源码和二进制保留。首次 `db3ac11` 的组件结果为 61/62，唯一失败是 GPUCC 测试遗漏 `invalid_mem` 日志类别，Linux 未运行；[原失败及同二进制修正对照](tb371fc/evidence/wholeboard-20261123-a47/gpucc-fixture-fix.json)仍保留原始结论。`7a5a5d4` 则在 NPUCC 测试运行前修正 core 空区域日志名称并取消；其取消状态也保留在 d790 公开回执中，没有改写为组件测试失败。

统一开发分支为 `tb371fc-verified-wholeboard-20261123-a47`。前一 `a813` 冻结检查点的公开计划是 **65 个 gate：61 个组件/解析检查和 4 次 Linux 启动**，另有独立 A77 / 共享 UFS PCI job。新增内容包括 GPU CX/GX、SMEM 交接及生产驱动观察器、AOP 参考对端、Kona CPUSS 分支和两个 USB PIPE 时钟 mux。统一提交 `a8136d1b1871d5a90aced67cc269dfb312be5c65` 已实际通过 **61/61 组件 + 4/4 Linux，以及独立 A77/UFS job**；见[本次 CI](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45179839601)及[按源码和二进制冻结的回执](tb371fc/evidence/wholeboard-20261123-a47/public-ci-a813.json)。

该 `a813` 检查点的二进制 SHA-256 为 `fc6c7cba342297fd86b4fdd9800f8e6a944e435a244f26091ef7e6c632ce03a9`，已下载 CI artifact 并直接计算包内文件哈希。两个观察器单元测试分别实际运行 17 和 14 项；它们使用模拟 monitor，同一次原厂运行的只读观察证据另见下文。

本次整合前已经实际完成的验证分别属于以下版本：

| 冻结版本 | 实际结果 |
| --- | --- |
| `79d7e978` | 同一板级二进制 56/56 组件 + 4/4 Linux；原厂完整 DT 下 SMEM 生产驱动成功路径 23/23，仍在 GCC `0x148198` external abort。该 run 的 A77 job 后来因新提交取消，不能称整个 run 全绿。 |
| `27171d77` | GCC CPUSS 来源再生成、真实父时钟/投票、迁移版本拒绝，以及 56/56 组件 + 4/4 Linux和独立 A77/UFS job 全部通过。 |
| `90c71325` | 原厂完整 DT 已越过 `0x148198`，下一实际异常为 USB PIPE mux `0x10f060`；这一版本的公共组件为 57/58，唯一失败是后来修正的生成器字典键，Linux 未执行，未改写为全绿。 |
| `e8122489` | AOP 严格 30 组、6 次成功迁移和 2 次配置拒绝；57/57 组件 + 4/4 Linux及 A77/UFS job 全绿。原厂驱动实际建立通道并消费 1 个 QDSS 请求，SMEM 仍为 23/23。 |

[原厂两次 GCC 对照](tb371fc/evidence/wholeboard-20261123-a47/native-progression.json)、
[原厂 AOP 只读观察](tb371fc/evidence/wholeboard-20261123-a47/native-aop-e812.json)
记录各自源码、二进制、输入和日志哈希。早期 AOP 观察器没有采集被拒绝请求的 payload；`a813` 的追加只读邮箱诊断已识别为 DDR 日志停止请求，详见[统一版本原厂运行](tb371fc/evidence/wholeboard-20261123-a47/native-a813.json)。观察器退出 0 不代表来宾启动成功。

**完整原生 DT 到达用户态、原厂 Android 和整板可用仍未通过。**
前一 `a813` 止于 GPUCC `0x03d90100`，`df667` 完成 GPUCC 注册后止于 NPUCC `0x09980000`，`d790` 完成 NPUCC 注册并到达 `boot_stats_init`，`0508` 越过 MPM 读取后止于 NoC QoS `0x016e9008`，`99ef` 越过 NoC 配置后止于 QSMMUv500 TCU `0x15182018`。各版本的原始回执均保留。DDR 日志请求与公开驱动的 panic notifier、实测 DT 属性相符；这是源码关联推断，没有将它列为启动前置依赖。

SMEM、AOP、SPMI 与电源交接的 reference 配置必须显式选择，不是原厂固件捕获或真实 PMIC/时钟频率测量。AOP 不执行固件，GX/CX 不执行 Adreno/GMU 命令。NPUCC 的数字时钟和 probe 成功也不等于 NPU/DSP 命令执行；后续改动必须获得自己的构建、组件和原厂运行回执。

[历史默认分支索引](tb371fc/evidence/DEFAULT-HISTORY-5fd52c1.md)保留原默认分支的五组冻结结果及原字节账本；没有用新源码覆盖旧回执。
[后续默认分支合并索引](tb371fc/evidence/DEFAULT-HISTORY-339b9cd.md)记录 `339b9cd` 的完整树差异、SMMU 迁移增量、两组新历史目录及保留原字节的旧账本。
此前 [GPU CX 检查点](tb371fc/evidence/gpu-cx-ci-20261123/checkpoint.json) 的未完成状态和初次失败也继续保留，不能代替上述后续实际验证。

## 先前 TCSR、AOP 消息内存与 REFGEN 增量验证（2026-11-23）

该轮冻结二进制 SHA-256 为 `2173fa4da68f8f769c53942b056124c5621e4714652c7f572deacc9440ec09cf`。
[55/55 公共 gate](tb371fc/evidence/boot-resources-20261123-1005/public-55/result.json) 实际通过，包含 51 组组件/解析检查和四次 Linux 启动。
[50 项专项检查](tb371fc/evidence/boot-resources-20261123-1005/workflow.json) 包括 TCSR 24 项、AOP SRAM 10 项和 REFGEN 16 项；十次迁移尝试包括七次成功传输及三次预期的配置不匹配拒绝。

TCSR 实现 32 个互斥槽的所有者和未知状态；AOP SRAM 实现 4 KiB 存储与真实 RAM 迁移；REFGEN 实现 CTRL5 bit 0 软件请求和状态有效性。
行为与边界见 [TCSR](tb371fc/docs/tcsr-mutex.md)、[AOP SRAM](tb371fc/docs/aop-msgram.md)、[REFGEN](tb371fc/docs/refgen.md)。

[两次原厂内核与完整 DT 诊断](tb371fc/evidence/boot-resources-20261123-1005/native-progression.json) 均失败：
默认未知 REFGEN 状态仍在 `refgen_kona_is_enabled` 读取处中止；显式选择尚未实测的 disabled reference 后，推进到 `gdsc_probe -> regmap_mmio_read32le`。
此前 AOP 64 字节描述符读取异常未在该冻结版本的这两次日志中出现；该轮尚未验证 AOP 固件、QMP 握手和电源服务。后续显式 reference 对端的握手与受支持命令消费结果见上方“本次整板推进与统一验收”；这不代表 AOP 固件执行。
**完整原生 DT 和 Android 启动仍未通过。** SMEM/SBL 交接、原生 PMIC 映射和未解析 regulator 等依赖继续保留为未完成项。

## 先前 QSEE 与 DCC 增量验证（2026-11-23）

该轮冻结二进制 SHA-256 为 `51fe61d35213a6602a8eaa865faba04bc85f88df341947ca8a2e6300a8ab5be6`。
[52/52 公共 gate](tb371fc/evidence/qsee-dcc-20261123/public-52/result.json) 实际通过，包含 48 组组件/解析检查和四次 Linux 启动。
[51 项专项检查](tb371fc/evidence/qsee-dcc-20261123/workflow.json) 包括 QSEE 12 项、真实迁移 6 项、DCC 新操作 16 项和原有 DCC 17 项。

QSEE 实现两组四位通知、mask/W1C、SPI 348/349 和状态迁移；DCC 实现有序单字 WRITE、平坦 LOOP 与单寄存器 RMW，执行真实内存事务。
[首次迁移脚本失败](tb371fc/evidence/qsee-dcc-20261123/initial-attempt/workflow.json) 与[脚本修正](tb371fc/evidence/qsee-dcc-20261123/harness-fix.json) 均保留；设备 C 代码和冻结二进制未因该修正改变。

未修改的生产内核加完整原生 DT 已越过 QSEE；[本轮实际故障](tb371fc/evidence/qsee-dcc-20261123/native-progression.json)
为 AOP mailbox 的 64 字节共享内存读取和 `refgen_kona_is_enabled+0x14`。
**完整原生 DT 和 Android 启动仍未通过。** QSEE 不是 TEE/SCM 执行实现，DCC 仍缺 CRC/ATB/CTI、嵌套循环等模式。
行为与限制见 [QSEE](tb371fc/docs/qsee-irq.md) 和 [DCC 操作](tb371fc/docs/dcc-operations.md)。

## 先前显示 RSC 增量验证（2026-11-23）

新增显示 RSC 的真实窗口和 SPI 129 接线，与三个 APPS 投票槽共享同一资源服务。
新二进制 SHA-256 为 `05c617462f87d5bacea9b86007ca8e37de5ed9a2644a5e3af5ac0d60485ec55b`。
[49/49 公共 gate](tb371fc/evidence/display-rsc-20261123/public-49/result.json) 已通过，包含 45 组组件/解析检查与四次 Linux 启动。
另有 [53 项 RPMh 专项检查](tb371fc/evidence/display-rsc-20261123/tests-final.json)，覆盖显示 11 项、显示迁移 6 项、APPS 22 项、APPS 迁移 5 项、文件配置 3 项及私有部分数据库的时钟 6 项。

同一原厂内核和完整私有设备树，配显式 SPMI reference 与部分 RPMh 文件配置，
已越过先前显示 RSC 和 SPMI 控制器访问异常；[该轮下一实际故障](tb371fc/evidence/display-rsc-20261123/native-progression.json) 是 `qsee_irq_probe+0x140`。
PMIC 路由、RPMh regulator 资源和 SBL/SMEM handoff 仍不完整。**原厂 Android 完整启动仍未通过。**
详细寄存器、共享投票和迁移边界见 [显示 RSC 文档](tb371fc/docs/rpmh-display-rsc.md)。

## 08:20 基线统一验收

[隔离构建的最终验收](tb371fc/evidence/verified-20261123-0820/final-verification.json)现已完成：
**47/47 公共 gate、9 种 Cortex-A77 配置合计 852 项 guest 检查及 2 项非法配置拒绝、15/15 PCI UFS 兼容测试全部通过。**
47 项包含 43 组组件/解析检查和四次完整 Linux 启动，用时 73.261 秒。
板级 QEMU SHA 为 `87f52240ff55e0b60a87e9a0dc57b76dff75e653f3dbe1d5dbdc64b2b7d9ac89`；
A77/PCI QEMU SHA 为 `2605ddb7a6c503cff9532bcd1f24a0945b76f5f148149170ee3af7813beeec29`。
运行时源码与二进制的前后哈希一致，冻结源码快照是 `9a80cddf1bd6ad8ecdbba34fa164acb5524e206b`。

[原顺序任务](tb371fc/evidence/verified-20261123-0820/initial-sequential-verification.json)保留失败状态：
PCI 首次 discovery 因冻结文件名附加后缀而得到零项测试。
将内容哈希相同的文件使用 QTest 要求的规范名称后，
[15 项兼容检查](tb371fc/evidence/verified-20261123-0820/pci-ufs/result.json)在 8.643 秒内通过。
发布前另有一处 DCC 纯格式修改，其[独立记录](tb371fc/evidence/verified-20261123-0820/dcc-style-only-after-verification.json)
说明忽略空白后的 token 完全一致；没有把冻结回执的源码哈希改写成后续文件哈希。

先前[42 项成功](tb371fc/evidence/resume-20261123/result.json)及
[旧二进制上的后续失败](tb371fc/evidence/public-ci-20261123/attempt-45-old-binary/result.json)均按原字节保留。

持续结果与限制见[持续验证账本](tb371fc/evidence/VALIDATION-continued-20261123.md)；
[原始验证账本](tb371fc/evidence/VALIDATION.md)及其原始哈希保持不变。

## 已执行的验证

以下是 2026-11-23（北京时间）的**独立冻结构建**回执。每个链接记录自己的二进制和输入哈希；检查数量表示该测试的断言数量，不是整机覆盖百分比，也不能把不同版本的通过记录直接当作一次整机验收。

| 验证 | 实际结果与证据 | 主要范围 |
| --- | --- | --- |
| 平板与启动布局 | [原始板级配置](tb371fc/evidence/android-board.json)、[扩展设备树/硬件清单](tb371fc/evidence/expansion-20261123/android-device-inventory.json)、[13 项运行时布局检查](tb371fc/evidence/boot-layout-20261123/runtime-layout-frozen4.json) | CPU affinity、内存段、GIC、UART、timer、设备依赖；布局检查保留原先重复 memory 节点失败及修复记录。 |
| Cortex-A77 | [9 配置、852 项 guest 检查及 2 项非法配置拒绝](tb371fc/evidence/cpu-a77-r1p0/result.json) | 架构寄存器、实际指令、特征开关、缓存 ID 描述和 PMU；不等于缓存时序或 DSU 模拟。 |
| GENI UART | [14 项寄存器/FIFO/IRQ 检查](tb371fc/evidence/expansion-20261123/revalidation-4/geni-qtest.json)、[6 项真实进程迁移](tb371fc/evidence/expansion-20261123/revalidation-4/geni-migration.json) | 包括修复后空闲 TX watermark 驱动下一次 TTY 发送；Linux 多次串口输出另有实际记录。 |
| GCC 与串口接线 | [19 项 GCC、5 项 GCC 迁移、6 项 clock/reset、4 项接线迁移](tb371fc/evidence/expansion-20261123/revalidation-4/summary.json) | 状态、父时钟、分频、门控、复位、GDSC 及未完成 TX/RX 恢复。 |
| CPU/MMIO timer | [精确计数及修前/修后回执](tb371fc/evidence/mmio-timer-live-status-20261123/) | 每虚拟秒准确 19,200,000 tick；独立 icount 参考、10 项 exact/10 项 legacy MMIO、真实 CPU 与 MMIO 迁移、模式不匹配拒绝，以及 16 次 CPU→GIC 线状态检查。 |
| SMMUv2 | [23/23 C QTest](tb371fc/evidence/smmuv2-20261123/qtest-result.json) | RAM 页表遍历、权限、fault、stream/context bank、ATS、TTBR1、无效模式；保留修前反例。 |
| SMMUv2 显式大页 profile | 默认分支 b5f4415 的 [CI 26/26](tb371fc/evidence/smmuv2-granules-20261123/ci-granules.json)与[扩展 28/28](tb371fc/evidence/smmuv2-granules-20261123/ct102-with-glib.json) | 16/64 KiB ATS/DMA、权限、故障及真实 v2→v3 接受/拒绝；默认仍为 4 KiB。原始回执随并行代码保留，新整板组合须另跑 CI。 |
| UFS 与 DMA | [56 项控制器/PHY/DMA 检查](tb371fc/evidence/resume-20261123/reports/32-ufs-register-dma.json)、[19 项 Linux UFS/SMMU 检查](tb371fc/evidence/ufs-bringup-20261123/linux-strict-iommu-19.json) | 真实 SCSI 块 I/O、strict DMA domain、flush/reopen、错误路径、Hibern8，以及宿主独立核对写入的 32 KiB 数据。 |
| RPMh reference/file | [22 项 reference/文件 ABI 检查、5 项迁移](tb371fc/evidence/expansion-20261123/revalidation-5/rpmh-summary.json) | TCS 执行、ARC/VRM/BCM 请求、IRQ、sleep/wake 输入、取消及非法请求不完成。reference 数据显式为合成数据。 |
| Watchdog | [16 项数字行为检查](tb371fc/evidence/watchdog-20261123/qtest-16.json)、[真实 pending-bite 迁移](tb371fc/evidence/watchdog-20261123/migration.json) | bark/bite、pet、counter phase、复位动作、IRQ 和错误频率拒绝；频率是软件配置证据。 |
| TLMM | [25 项 C QTest](tb371fc/evidence/tlmm-20261123/qtest-result.json)、[15 项真实迁移检查](tb371fc/evidence/tlmm-20261123/migration-result.json) | 180 GPIO、边沿/电平 IRQ、输出/使能、专用 UFS reset、显式 boot handoff；四次进程迁移。 |
| IMEM / PS_HOLD | [7 项检查，含实际迁移](tb371fc/evidence/boot-resources-20261123/qtest-migration-7.json) | 可写 SRAM、guest-owned handoff 字段、reset retention 和数字 PS_HOLD 输出；没有伪造 PMIC 关机策略。 |
| Linux RPMh provider | [23 项 guest 与 3 项宿主检查](tb371fc/evidence/rpmh-linux-20261123/result.json) | 显式 reference profile；RSC/clk-rpmh 绑定、CCF 19.2 MHz、真实 TCS IRQ、完成的 XO 事务与清除的 pending IRQ、UFS/SMMU 和宿主磁盘核对。 |
| DCC | [17 项原有检查](tb371fc/evidence/qsee-dcc-20261123/dcc-existing/report.json)及[16 项新增操作检查](tb371fc/evidence/qsee-dcc-20261123/dcc-operations/report.json) | 实际 READ/单字 WRITE/平坦 LOOP/单寄存器 RMW 与 pending 迁移；reference 身份和时序，CRC/ATB/CTI 等仍缺。 |
| QSEE IRQ | [12 项行为检查](tb371fc/evidence/qsee-dcc-20261123/qsee/report.json)及[6 项真实迁移](tb371fc/evidence/qsee-dcc-20261123/qsee-migration/report.json) | 两组通知锁存、屏蔽、W1C 与真实 SPI 348/349；不执行 secure firmware。 |
| PDC | [18 项 C QTest](tb371fc/evidence/verified-20261123-0820/public-47/30-pdc-c-qtest/case.json)、[10 项迁移检查](tb371fc/evidence/verified-20261123-0820/public-47/42-pdc-migration/fixtures/data/result.json) | 已在同一隔离构建实际通过；native VERSION 仍未知，旧 17/9 回执保留。 |
| IPCC | [16 项消息/IRQ/迁移检查](tb371fc/evidence/verified-20261123-0820/public-47/37-ipcc-messages/fixtures/data/result.json) | 实际 GIC SPI 229 传播、mask/clear 和迁移；未连接的远端固件不伪造回复。 |
| SPMI / PDC / GIC | [SPMI 25 项](tb371fc/evidence/verified-20261123-0820/public-47/39-spmi-pmic-arb/fixtures/data/result.json)、[真实连线与迁移 8 项](tb371fc/evidence/verified-20261123-0820/public-47/40-spmi-pdc-gic-route/fixtures/data/result.json) | 显式 reference 映射和测试端点；事务、剩余 deadline 与 SPI 481 传播已测，原生 PMIC 行为未由此证明。 |
| 生产内核无串口验收 | [8/8 用户态检查与真实 guest shutdown](tb371fc/evidence/production-gdb-exact-20261123/result.json) | 未修改的 4.19.157 生产 Image、8 GiB、8 CPU、生成 DT、测试 PID 1；GDB 只读普通 guest RAM 结果后恢复执行。 |

TLMM、IMEM/PS_HOLD、普通 Linux UFS19 和 reference RPMh 联合 Linux23 已在冻结构建 7（QEMU SHA `f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b`）实际通过。新 kernel SHA 为 `c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`。这些组件和生成 DT 的成功仍不证明完整原生 DT 或 Android 启动。08:20 基线集成重放、实际二进制和各 gate verdict 见[该轮回执](tb371fc/evidence/verified-20261123-0820/final-verification.json)；分项与后续证据见[持续验证账本](tb371fc/evidence/VALIDATION-continued-20261123.md)。

### 三种启动结果的区别

- **公开主线 Linux**：固定版本、无补丁的 upstream 输入，正式 `ttyMSM0` console 接管，用户态检查和 UFS/SMMU I/O 均有实测。UFS 的专用 I/O gate 在检查 guest verdict 和磁盘后由宿主 QMP 结束；它不测试 guest shutdown。
- **未修改的生产内核 + 测试 initramfs**：已有八项用户态检查和真正的 QMP `guest-shutdown` 事件。生产配置没有串口 console，验收通过只读 GDB checkpoint 取出完成掩码、uname、在线 CPU、计时和原样探针输出，再继续 guest 关机。见 [guest-acceptance.md](tb371fc/docs/guest-acceptance.md)。
- **生产内核 + 完整原生 DT / Android 系统**：实际诊断已越过 watchdog、IMEM/restart 和 DCC probe，并由未改驱动编程 DCC 列表 3/6/7；后续具体故障按冻结构建逐次记录。见[逐次诊断回执](tb371fc/evidence/native-dt-progression-20261123/progress.json)。这没有证明 DCC 完整捕获或完整 DT 到达 PID 1，Android userspace、显示、输入和网络也尚无端到端成功记录。

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
| SMMUv2 | apps/GPU 两个架构窗口、非安全 AArch64 stage-1、默认 4 KiB 与真实 DMA 翻译；16/64 KiB 需[显式架构 profile](tb371fc/docs/smmuv2-granules.md)，原生能力未测量。apps TCU/TBU 另由有限伴随模型实现；stage 2、完整 QSMMU v500 与 GPU 执行仍未完成。 |
| UFS | Sysbus HCI、Qualcomm wrapper、QMP PHY、GCC 依赖、SMMU DMA 与 SCSI 后端。使用显式测试磁盘。ICE、RPMB 认证、完整原生 LU 启动属性、全部错误/复位模式和物理 PHY/闪存时序仍未完成。 |
| RPMh | `rpmh-profile=off` 为默认值；`reference` 和 `file` 必须显式选择。file 解析外部 cmd-db 并核对内容；未知资源不会自动获得成功 ACK。它不执行 AOP 固件，也不模拟真实 PMIC 电压。 |
| 迁移 | 各数字组件的实际回执分别绑定原源码与二进制；组件测试关闭 UFS，**上游 UFS core 仍阻止整板迁移**。SMMUv2 原 [18 项进程迁移回执](tb371fc/evidence/smmuv2-migration-20261123/publication.json)使用 v2；当前 v3 增加 granule profile 相等检查，v2 仅能进入默认 profile，v1 仍拒绝。并行分支的真实跨版本验证见上表，不替代新组合的 CI。 |

地址、属性、启动接口、生成 DT 与原生 DT 的差异见 [machine.md](tb371fc/docs/machine.md)。独立设备文档和来源索引见 [SOURCES.md](tb371fc/SOURCES.md)。

## 构建和公开验收

[Containerfile](tb371fc/Containerfile) 提供开发环境。示例不访问真实平板或已有磁盘：

```sh
git clone --branch tb371fc-verified-wholeboard-20261123-a47 https://github.com/cyjin-yl/qemu-tb371fc.git
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

# 八项用户态 + UFS/SMMU 十九项 + RPMh 联合二十三项，均为 8 GiB / 八核
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
