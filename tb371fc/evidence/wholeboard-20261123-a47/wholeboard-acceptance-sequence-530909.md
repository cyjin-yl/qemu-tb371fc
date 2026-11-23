# 整板可用：最小验收差距与下一步取证

审查源码：`530909a61f59ac6bf4a644ac0d13f4d1fc9c95ea`，tree `1c6f028316c75f19b416a3f885534433360f7637`。这是只读验收规划；没有新增测试、设备模型、QEMU 运行或实机采集。74 项组合在本文审查时尚无实际结果，本计划不预判其成功。

## 当前已证明到哪里

- 52492a 已通过 68 个组件 gate 和四个 Linux gate，后者均为 clean Linux 6.12.114／生成的功能子集 DT：一般用户态、无 LU 用户态、UFS/IOMMU I/O、UFS/IOMMU/RPMh。独立 A77/PCI UFS job 也不替代完整原厂软件。
- 生产 Image `ef9e7b5c…1cf0f` 的历史 8 项 PID1 检查和 guest shutdown 使用生成 DT。完整原厂 DT `5114262a…6207` 的最近一次同 CI 二进制诊断仍在内核 initcall 中崩溃：CPU4/PID1，`qcom_cpufreq_hw_driver_probe+0x408`，直接物理读取 `0x18591100`。不是已执行验证 /init 的失败。
- TSENS 采样、SMEM 发布、AOP QDSS、NoC 配置、TCU/TBU 资源注册各有受限证据；native SMMU DMA、GPU/NPU 执行、完整 DT 用户态及 Android 仍没有成功证据。显式 reference 数据与真实固件交接分开记录。

依据：`tb371fc/evidence/wholeboard-20261123-a47/public-ci-52492a.json`、`tb371fc/evidence/wholeboard-20261123-a47/native-52492a.json`、`tb371fc/evidence/production-gdb-exact-20261123/result.json`。

## 按此顺序收证据

| 阶段 | 下一份回执必须回答的问题 | 当前缺口 |
| --- | --- | --- |
| 1. 同二进制完整 DT 推进 | 使用通过正式 CI 的确切 QEMU，绑定 Image、完整 DT、initrd、配置和输出哈希；记录 CPUFreq 是否真正继续以及下一首次异常。保留真实 printk、异常和 stopped QOM。 | 74 组合原厂结果尚未发生；QOM requested/effective/计数快照本身不证明驱动成功、EL0 或实际频率。 |
| 2. 完整 DT 的真实验证用户态 | 复用已保存的 8-check initrd/ELF，EL0 checkpoint、3120-byte RAM record、8/8 检查与真实 guest shutdown 分项判定。 | 现有成功仅在生成 DT；`Run /init`、无 panic、超时后 exit 0、probe 日志都不够。 |
| 3. 原厂 UFS/IOMMU 存储 | 完整 DT／生产内核连接新空白合成 LU0，保存真实驱动绑定、translated DMA、读写/flush/reopen/SCSI 结果及宿主独立 32 KiB 字节比较。 | 现有 19-check 成功是 clean Linux；当前原厂诊断没有挂 LU。原生 ICE/RPMB/boot-LU 等仍不由普通块 I/O 证明。 |
| 4. 原生 GPU、显示和输入 | 依次证明原生驱动 probe、真实提交/完成/故障、SMMU 数据路径、固定图像输出，再证明 DPU/DSI/DSC/面板和输入事件到来宾应用的往返。 | 时钟/电源/显示 RSC 不执行渲染；KGSL 的 `gcc_gpu_memnoc_gfx_clk` 仍 off/-16，KGSL vendor 窗口与 Adreno/GMU/HFI、DPU/面板/输入仍缺。 |
| 5. Android 与生命周期 | 用来源匹配的 Android 用户空间/存储内容，保存 init/挂载、必要服务、zygote/system_server/SurfaceFlinger、真实来宾 boot-completed 与可操作 UI 的同次证据；再保留关机/重启和持久数据结果。 | 测试 /init 不是 Android init。原厂分区/安全固件依赖、显示/触摸/连接等尚未形成端到端结果。direct-kernel 启动不验证 PBL/XBL/ABL/AVB/TrustZone；任何替代或合成服务要显式列出。 |

阶段 2 若用户态记录完整而关机失败，应分别记为“完整 DT 验证用户态已执行／8 项通过”与“生命周期未通过”，不得丢弃正向证据，也不得把整个 gate 写成通过。组件迁移不能提升为整板迁移：UFS core 当前阻止整板带存储迁移，已有组件测试大多关闭 UFS。

## 直接复用已有夹具

所有精确 hash、协议与源码身份已保存在 `tb371fc/evidence/wholeboard-20261123-a47/native-userspace-fixture-contract.json`，无需重做：

- **8-check 通用夹具**：`/work/gdb-smoke/exact-production-1/initramfs.cpio.gz`，SHA256 `88c075fc15487f3d6dc8e7cb1b61a35aa74a8f25ec6001a7d883c9623f86d7f6`；配套 ELF 为同目录 `init`。checkpoint 为 EL0 VA `0x08000000`，result 为 EL0 VA `0x004a0040`。要求 TBFCRES1/version1、completed1、checks/pass/fail=`ff/ff/0`、有效原样 counter probe，以及 QMP `SHUTDOWN guest=true reason=guest-shutdown`。采集器注册/移除 TCG breakpoint 并 stop/resume，不改 guest RAM/寄存器/PC；要披露这项调试控制。不要原样运行会重编 initrd、缺少本轮 reference 配置的旧 main。
- **当前 UFS /init**：`/work/ufs-iommu-4/initramfs.cpio.gz`，SHA256 `8c2f582b72f19ba6043af7e85f91a3936c3c8fcb3c2a306ae4f76efe6d72ae7e`。新运行的 CHECK/RESULT marker 即使 FAIL 也能证明该程序执行；无 console 时 marker 缺席不能反证。它没有通用 RAM ABI，也不主动关机。当前无 LU 配置不能要求 19/0。
- **存储复用选项**：新空白 64 MiB 合成盘，file/raw blockdev 加 `ufs-lu,bus=ufs0,drive=<raw>,lun=0`；宿主核对 offset 4 MiB、length 32 KiB，pattern SHA256 `faf3511fec2180665df534811d7ed01fdf14127f1d0596c46b34fb9ba6ee466e`。旧 /init 固定五个生成 DT 的 sysfs 路径，先核实际完整 DT 的驱动身份；路径不符必须保留该夹具失败并按来源另作适配，不能跳过绑定断言或沿用旧 19/0。整个选项尚未执行，不使用平板原分区。

## 引用索引与称谓

- `README.md`：当前组合与 52492a 已执行边界。
- `tb371fc/docs/ci.md`：四个 clean Linux gate 的实际范围。
- `tb371fc/docs/guest-acceptance.md`：8-check/EL0/真实 shutdown。
- `tb371fc/docs/machine.md`：direct boot、显式 reference、无 LU 默认、整板迁移与未实现外设。
- `tb371fc/docs/ufs-sm8250.md`：真实 I/O、host payload 与 ICE/RPMB/迁移边界。
- `tb371fc/ROADMAP.md` 第 4/6 节：原生 GPU/显示链和完整 Android。

最早可单独确认的新里程碑是“原厂生产内核＋完整 DT 的验证用户态”；随后可确认“完整 DT 的合成盘存储可用”。两者都不能独自改写为“整板 Android 可用”。ROADMAP 有历史未更新的“SMMU 独立迁移”未勾项及“71 项公共成功”措辞；应随下一冻结结果澄清版本范围，不影响现有严格门槛。
