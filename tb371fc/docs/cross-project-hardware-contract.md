# TB371FC：Linux、QEMU 与 XNU 共用的硬件契约

记录日期：2026-11-24（Asia/Shanghai）。初始文档核对的已发布 QEMU 基线为
`69ca28dec6add998d252fa6436dfcc9b9cab6e36`，Linux 研究基线为
`ec42c484`，UniXNU 基线为 `573ea14a917648f38fd3f2f920020aaf7a28f6ff`。
这些是初始文档的快照，不替代其他并行分支的新运行回执。

同日迁移进展补记：QEMU [PR #4](https://github.com/cyjin-yl/qemu-tb371fc/pull/4)
和 [PR #5](https://github.com/cyjin-yl/qemu-tb371fc/pull/5) 已合并。
下文 UFS 现状依据 [03cd8843 原始验收](ufs-migration.md#original-ufs-acceptance)
和 [839ae467 后续验收](ufs-migration.md#profile-and-qmp-drain-follow-up)，
各轮源码、二进制与输入范围保持独立；合并本身不构成新的运行回执。

本机后续输入见 [DT 入口证据与 Novatek 错误路径](native-entry-evidence-20261124.md)：
记录 Android 报告的 DTB/DTBO 索引、备份条目、内存保留区及 Linux 驱动修复的实际范围。
这些资料用于后续模型实现，不增加 QEMU 或本机原生 Linux 验收通过数。

## 决策与分工

主线 Linux 加主流发行版是 TB371FC 的长期通用系统方向。现有 Android/chroot
继续承担已验证的使用场景；源码查找、Linux 板级补齐、QEMU 模型和 UniXNU 路线 A
可并行。没有必要从零重写 SM8250 的所有驱动，也不能把某个系统的成功直接计入另一个系统。

| 仓库 | 负责的接口和证据 | 对应文档 |
| --- | --- | --- |
| TB371FC-6.12 | 本机硬件清单、upstream 支持核对、DTS/binding/driver、Linux 启动和整机验收 | [主线策略](https://github.com/cyjin-yl/TB371FC-6.12/blob/docs/mainline-strategy-20261124/docs/MAINLINE_STRATEGY.md)、[驱动路线](https://github.com/cyjin-yl/TB371FC-6.12/blob/docs/mainline-strategy-20261124/docs/DRIVER_ROADMAP.md) |
| qemu-tb371fc | 软件可见寄存器、DMA、IRQ、固件消息、状态机及可重复的错误/恢复测试 | 本文、[组件来源](../SOURCES.md)、[整机路线](../ROADMAP.md) |
| UniXNU-tb371fc | XNU/IOKit/provider 接入、普通 ARM64 路线 A、iPadOS 27 与独立 macOS 27 验收 | [共用硬件资料](https://github.com/cyjin-yl/UniXNU-tb371fc/blob/docs/mainline-hardware-contract-20261124/docs/CROSS_PROJECT_HARDWARE.md) |

共用的是硬件事实、协议定义、来源和测试向量。Linux DRM/ASoC/remoteproc 驱动
不会直接成为 XNU kext；QEMU 的成功响应也必须来自有定义的设备状态机。

## 新鲜实机基线与证据优先级

同日[Android 只读摘要](https://github.com/cyjin-yl/TB371FC-6.12/blob/docs/mainline-strategy-20261124/docs/evidence/2026-11-24-live-android-baseline.json)
确认本机为 TB371FC / spinel，Android 14 / ZUI 16.0.474，Linux 4.19.157-perf+。
采集仅读取属性、配置、cgroup、sysfs 和安装包数据库；未取原始输入事件或执行设备寄存器写入。

| 观察 | 工程上可以据此确定的内容 | 仍待证明 |
| --- | --- | --- |
| `spi0.0` 绑定 `NVT-ts`；触摸和笔各自有 input 设备 | Novatek SPI 路径和两个输入接口 | 精确固件/协议版本、多点、笔压/倾角、错误恢复与主线/XNU运行 |
| 键盘三类接口为 `0018:17ef:6175`；Precision Pen 3 蓝牙接口为 `0005:17ef:612b` | I2C 键盘和蓝牙笔附加事件应与屏幕笔通道分别建模 | attach/detach、供电、唤醒、报文和各操作系统 HID 行为 |
| `tfa98xx` 绑定 I2C `6-0034` 至 `6-0037` | 有四条实际 Android 功放驱动绑定 | 精确芯片变体、每路校准、DSP 配置及物理音频；不能用 DT 中的 WSA881x 候选替代实际绑定 |
| `ktz8866a/b` 绑定 `2-0011` / `4-0011`；`mm8013` 绑定 `1-0055` | 双背光和电量计的实际路径 | 亮度/电源时序、电量精度和休眠恢复 |
| QCA6390 的 `cnss2` / `bt_power`；FSA4480；SMB1396 子节点与 SMB1398 charger driver | 已知无线、Type-C 音频切换和充电链线索 | 固件/校准、射频、PD 合同、快充和热保护；`USB_PD` 类型名不是已协商合同 |
| `goodix_fp` 绑定、`qti-haptics` input | 新增待核对的平台线索 | 确切器件、是否实际配置可用，以及接口功能；不采集生物识别数据 |
| CPUinfo 未暴露 PAC/BTI 标志，implementer/part 包含 Qualcomm `0x51 / 0x805` 与 Arm `0x41 / 0xd0d` | 当前内核导出的用户态能力视图 | 完整 per-core ID/ISA 审计及 Apple ABI；不能用模型开启能力替代物理证明 |

历史逐核 MIDR/REVIDR 记录见 [cpu.md](cpu.md)；本轮 CPUinfo 摘要没有重新采集完整逐核 MIDR。

设备树声明、driver 绑定和功能测试分别记录。通用 `kona QRD` DT 名称、旧社区作者
的 BOM、相近型号或同一 SoC 都不优先于本机的实际观察；不同观察之间的冲突必须保留。

## 每个共用契约必须携带什么

新契约采用现有[来源](../SOURCES.md)与[证据](../evidence/README.md)规则。
至少记录以下字段，未知值保留为未知：

| 字段 | 最低内容 |
| --- | --- |
| 身份 | 板型/固件 build、器件或协议版本、实例和总线地址、DT路径；芯片型号的证据等级 |
| 来源 | 观察方法、读取范围、日期、固定源码 commit、原始或公开派生文件摘要、许可证 |
| 访问语义 | 寄存器宽度/端序、可读/可写位、W1C/clear-on-read、保留位、访问权限和错误返回 |
| 状态依赖 | 上电/复位、clock/reset/regulator、DMA/IOMMU、IRQ、firmware ownership、并发顺序 |
| 生命周期 | probe/start/stop、热插拔、取消/超时、reset、suspend/resume，以及仍未知的状态 |
| 测试输入 | 正常/边界/错误序列、期望输出的来源、不得被测试工具修改的观察对象 |
| 实现映射 | Linux driver/binding、QEMU model/test、XNU provider/driver 各自源码路径与版本 |
| 运行证据 | 实际被测二进制/内核/DT/固件摘要、命令、stdout/结果、失败及跳过项 |

记录类别应写清楚：`upstream-source`、`owner-android-observation`、
`community-device-report`、`synthetic-reference`、`qemu-execution`、
`owner-linux-execution` 或 `owner-xnu-execution`。
存在源码不等于运行；在 Android 枚举不等于新操作系统驱动通过。

不要读取仍在运行设备上的任意 MMIO 来补表。先用公开驱动、现有 DT/sysfs、
已有授权记录和可独立运行的合成输入确定契约；需要新的硬件观察时单独约定读取方法。
原厂固件、Apple 系统文件、整机私有镜像和个人数据继续留在仓库外。
代码复用保留各文件许可证与作者；可公开派生资料不冒充完整对应源码。

## 复用现有模型，补齐真实差距

下表是后续工作入口，不把待实现项记成通过：

| 领域 | 本仓库可复用的现有契约 | 下一段可交付工作 |
| --- | --- | --- |
| CPU/IRQ/timer | [CPU](cpu.md)、[timer](mmio-timer.md)、GIC/PSCI 启动 | 保留 A77 与小核证据差异；核对新 kernel 的中断、计数器、启动保留区和多核路径 |
| 时钟/电源/固件 | [GCC](gcc-sm8250.md)、[GDSC](gdsc.md)、[RPMh](rpmh-sm8250.md)、[SMEM](smem-reference.md) | 从实际失败路径补依赖和所有权；保持 native 事实与 reference 初始化独立 |
| DMA/UFS | [SMMUv2](smmuv2.md)、[UFS](ufs-sm8250.md)、[页粒度](smmuv2-granules.md)、[空队列检查点](ufs-migration.md) | 让 Linux/XNU 使用同一读写、越界、fault、撤销映射和 reset 向量；补未完成存储 I/O、MCQ 与整板迁移的剩余缺口 |
| 触摸/笔/键盘 | [GENI](geni-uart.md)、[TLMM](tlmm.md)提供部分基础 | GENI UART 不等于完整 SPI/I2C；先实现实际控制器，再加入 Novatek 与 I2C-HID 的正反例、热插拔和唤醒 |
| 显示/Adreno650 | [GDSC](gdsc.md)数字请求边界 | 分开补 DPU/双DSI/面板、背光、GMU/HFI、ring/fence、SMMU；用实际提交和图像验收 GPU |
| Wi-Fi/BT/音频/协处理器 | IRQ/DMA 与 [IPCC](ipcc.md)等通信基础 | 各自建立 PCIe/MHI、GLINK/RPMsg/QRTR、DSP/codec 的协议与生命周期，缺少远端服务时保留真实错误 |
| 电池/充电/温控/休眠 | [SPMI](spmi-pmic-arb.md)、[PDC](pdc.md)、[watchdog](watchdog.md) | MM8013/charger/PMIC/唤醒状态机；功能模拟单列，功率、温度和真实待机电流仍须物理设备测量 |

QEMU 不以固定成功 ACK、未标注的全零寄存器、跳过错误、其他 Adreno 世代的身份或
主机 GPU 转发替代 A650 完成证据。

迁移必须绑定实际参与的设备与队列状态。此前关闭 UFS 的组件迁移保留原配置与范围；
`qcom-sm8250-ufs` wrapper 的空 legacy 队列检查点已通过 21 组检查、3 次真实进程
迁移和 7 个拒绝场景，覆盖非恒等 SID `0xe0` DMA 与外部共享磁盘 I/O。
[839ae467 后续回执](../evidence/ufs-migration-20261123/verified-839ae467/ufs-migration-result.json)
另取 3 份真实 profile 流作拒绝依据，不计为迁移；6 个被拒 receiver 退出后均将
QMP 读至 EOF 再检查无 RESUME，该完整事件审计仅属于后续回执。
通用 `ufs` PCI 与 `ufs-sysbus` 仍不可迁移；未完成 SCSI/MCQ 存储请求未被序列化，
完整原生整板迁移仍待单独验收。

## 让启动与 systemd 验收各自准确

本仓库[生产内核用户态 gate](guest-acceptance.md)曾让未改动的原厂 Image
在**生成的功能子集 DT + 测试 initramfs**下通过八项 PID1 检查并真实关机。
它不证明完整原厂 DT、Android、主线桌面或物理平板的驱动完成。
完整 DT 的后续诊断在其他并行工作中持续推进，以具体回执与 binary hash 为准。

本机 live config 的 `PID_NS`、`USER_NS`、`CGROUP_PIDS`、
`FHANDLE`、`DEVTMPFS` 和 `KEXEC` 关闭；Android 持有主要 v1
控制器，观察到的 v2 root controllers 为空。已安装 Debian 的 systemd 版本是
`257.7-1`。这限制在 Android 宿主内启动完整容器管理器，但并不否定原厂内核
在 QEMU 里作为整台 guest 的 PID1 执行测试 initramfs；两条路径不是同一验收。

主线候选的独立启动应新增实际发行版 initramfs/rootfs、systemd PID1、统一 cgroup
v2、设备管理、登录/网络与关机的集成回执。systemd 管理生命周期，不实现缺失的
GPU、触控、充电或休眠驱动。宿主 Android 的 cgroup 不得为了制造容器成功被全局改绑。

## UniXNU / macOS 27 的复用边界

UniXNU 保持已选路线 A：离线 arm64e 转普通 arm64，CPU 原生执行。
当前通用 XNU 编译链接成功不等于设备 profile、完整 provider 闭包或 27 的启动成功。
Linux 的成熟驱动用于核对硬件行为，XNU 自行完成 IOKit、DMA、同步、电源与图形接口。

QEMU 可重放指定 ABI 和设备协议的测试；开启可配置 PAuth 或 16/64 KiB 翻译能力
不会给 TB371FC 物理 CPU/SMMU 增加能力。macOS 27 的输入、全包 ISA/指针协议、
内核 ABI、根卷、launchd、WindowServer/Metal 和整机性能分别验收。
具体版本和门槛见[UniXNU 共用资料](https://github.com/cyjin-yl/UniXNU-tb371fc/blob/docs/mainline-hardware-contract-20261124/docs/CROSS_PROJECT_HARDWARE.md)。

## 本次文档验证范围

本文及同日同步只维护跨仓库契约、入口与已归档验收范围；核对引用、路径、JSON 来源和 diff。
没有更改模型代码、运行新的整板启动或提升任何硬件通过状态。
[厂商源码研究及请求状态](https://github.com/cyjin-yl/TB371FC-6.12/blob/docs/mainline-strategy-20261124/docs/SOURCE_PROVENANCE.md)
与上述工程工作并行维护。
