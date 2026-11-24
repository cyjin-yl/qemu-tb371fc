# 本机 DT 入口证据：供 QEMU 消费的边界

日期：2026-11-24（Asia/Shanghai）。本次文档基于 QEMU 默认分支
`8babe85b1cd7990a1a113b223d51ec8a7732fed5`。本页接入 Linux 工程取得的
本机 Android 观测、备份格式检查和驱动修复证据；没有执行新的 QEMU gate。

Linux 的原有硬件台账仍为 **44 项：12 项阻塞、32 项未运行、本机原生 Linux
通过数 0**。社区 R226、主机编译、离线 DT 运算、QEMU 运行和物理设备运行分别记账。

## 已取得的 DT 对应关系

原始公开记录保存在 Linux 仓库的
[live Android 摘要][live]与[备份 DT 检查][backup]。
只保留板型、数字 ID、内存描述和摘要；原厂镜像、原始 DT、启动参数、序列号和
私有备份不复制进本仓库。

| 证据 | 实际观察 | 允许的结论 |
| --- | --- | --- |
| 2026-11-24 10:23:24 的 Android 属性及内核命令行白名单 | 两个视图一致报告槽位 `_a`、`dtb_idx=0`、`dtbo_idx=2` | 当前 Android 报告的索引一致；没有重放 ABL 选择算法 |
| 已备份 `boot_a` 的 DTB 索引 0 | 538,388 字节；`qcom,msm-id=<356 131073>`，基础树 `qcom,board-id=<0 0>` | 是与报告索引对应的 kona v2.1 基础树候选；基础树本身并未变成 QRD 完整树 |
| 已备份 `dtbo_a` 的表项 2 | 502,232 字节；QRD，`qcom,board-id=<11 0>`，compatible 为 `qcom,kona-qrd / qcom,kona / qcom,qrd` | 覆盖项的板标识与 live 树相符；表项编号和匹配标识不能证明逐字节实际选择 |
| live FDT | 973,052 字节、4,276 节点；SoC ID 与基础树一致，板 ID 与上述覆盖项一致 | 支持分层对应关系；启动器修改、覆盖应用及加载位置另行验证 |

四份 boot/recovery DTB 组件各含 **4 份串联 FDT**；其中索引 3 是一份 173 字节、
没有 root model 的小树，不能把“第几份 FDT”当成板型选择规则。
`boot_a` 索引 0 的字节与另外三份镜像的索引 0 不同；即使 release 或 SoC ID
相同，也不能互换来源。两份 recovery-DTBO 和两份 dtbo 分区的有效表体均为
**14 项、4,299,963 字节**，表体 SHA-256 为
`279910628e61cad149ccdb16ae0aa0a3a2f46441e21132ac3a1323e565219679`。
分区余下字节仍作为未解释尾部记录，没有据此验证 AVB。

[确定性对应报告][correlation]已对实际公开输入运行：先要求报告索引不存在重复、
冲突、非法值或越界，再按槽位取出对应条目。35 个 reserved-memory 节点的已采集
元数据与基础树一致；基础树的板 ID、model、compatible 及 RAM 描述与 live 树仍
分别比较，没有因 SoC ID 一致而把整份树判为相同。该报告明确保持
`runtime_abl_replayed=false`、`per_byte_runtime_selection_verified=false`。

### 独立 libfdt 核对与离线合成

[独立 libfdt 实际回执][libfdt]于 2026-11-24 10:35:27 完成。DTC 1.7.2 的
`fdtget`／`fdtoverlay` 共执行 136 条命令；基础树 56 项、覆盖项 7 项的同输入字段
读取均与先前解析结果相等，未导入项目自身的 DT 解析器。这里的 63 项是解析器之间
的字段核对，不是硬件验收项。

`fdtoverlay(base0, overlay2)` 实际返回 0，但产物与 live 树仍有四组公开字段差异：

| 字段 | 离线合成产物 | live Android |
| --- | --- | --- |
| root compatible | `qcom,kona` | `qcom,kona-qrd / qcom,kona / qcom,qrd` |
| root model | `kona v2.1 SoC` | `kona QRD` |
| Qualcomm ID 组 | 板 ID `<0 0>` | 板 ID `<11 0>`；SoC ID 相同 |
| `/memory/reg` | `(0, 0)` 占位 | 下述两段 RAM |

因此普通离线覆盖应用没有复现 live 树的 root 修订与真实 RAM。具体启动器修订和
入口交接仍须独立验证；QEMU 不能把这个合成产物标成最终板级 DT 或 ABL 重放结果。

### 内存描述不能直接成为可分配 RAM

live DT 的两个 `/memory/reg` 区间如下，端点采用半开区间：

| 起点 | 终点（不含） | 长度 |
| --- | --- | --- |
| `0x80000000` | `0xbbb00000` | `0x3bb00000` |
| `0xc0000000` | `0x280000000` | `0x1c0000000` |

备份中的基础树仍含 `(start=0, size=0)` 内存占位，不能单凭基础树初始化真实
内存布局。live 树列出 35 个 reserved-memory 子节点，其中 23 个静态、12 个
动态请求。静态区合并后有 7 个区间；显示 dump 与 continuous splash 在
`0x9c000000` 重叠 8 MiB。重复声明不能简单相加，动态请求也不能直接删除。
这些是 DT 所描述的排除项，尚未涵盖全部启动器、固件、正在运行设备及后续分配的
内存所有权。

live DT 两处属性对齐填充不是零：偏移 821109、821537，各 3 字节填充中有
1 个非零字节。严格格式检查保留原始失败；显式 forensic 读取仅用于取得有界元数据，
结果保持 `format_compliant=false`。备份 DTB/DTBO 的严格解析没有启用这个例外。
模型输入不能因设备能运行 Android 就忽略格式、尺寸或内存排除检查。

## 可复用的 Novatek 错误路径

Linux 已合并的 [PR #9][novatek-pr] 修正 WDT 历史诊断在选页、SPI 读取或分配失败时仍继续
使用缓冲区的问题。补丁保留首个错误、尝试恢复 event page，并让已有固件重载调用
继续执行。[固定来源报告][novatek-report]记录了实际 C 的 24 项修复后检查、3 处原版
缺陷复现，以及完整 ARM64 外部模块编译；它们属于主机回归和构建证据。

新模块为 698,512 字节，SHA-256
`998623b5b8d5666493162b04b01e3b3453777b3586d644d2296b23674881dc69`，
**未签名、未安装、未加载**。它与冻结的 84 模块 R5 集合保持独立。
完整 WDT/ESD 重载事务、笔状态恢复和物理触控行为仍未验收。

QEMU 可以复用这些选页、失败返回、恢复顺序和持续重载的测试场景，但应先有对应的
GENI SPI 与 Novatek 协议模型。合成的读取失败或分配失败并不证明物理 SPI 的错误时序，
主机驱动回归也不算本仓库新增模型测试通过。

## 对下一段模型实现的要求

1. 每份 guest 输入显式标注来源：生成 DT、备份基础树、指定覆盖后的离线树或
   live 观测。记录精确摘要、槽位和索引，不把相同 ID 当作相同字节。
2. 完整板输入分别记录 RAM banks、静态排除区、动态保留请求和 loader 使用区；
   缺失部分保持未限定，不用一个连续 RAM 范围覆盖间隙及保留区。
3. 模型或加载器完成 overlay 应用时保存实际输入、工具／算法版本和产物摘要。
   离线合成成功、模型启动、原厂 ABL 选择及平板原生启动仍是四种结果。
4. 后续 QEMU CPU/UFS/整板 gate 沿用各自实际源码和运行回执。本页不重评分已有
   UFS 迁移，也不触碰并行 wholeboard 工作。

证据摘要：live FDT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`；
公开 live JSON SHA-256
`e071f91b43a063ff6f96cb401269469e20703ab695e64dd37a1d5b86113ba745`；
公开备份检查 JSON SHA-256
`3ac35a67582837d4eb7009e224dd8e4beb69377a2c832549466eb4cc3f21d5e6`。
对应报告 SHA-256 为
`ac050fe46cf5df81b672cd22fe45605f4962ae6dbc905ca3ee9791089b69c654`。
独立 libfdt 回执 SHA-256 为
`49286ad739400926b17b1dd6732a09b6be1686f0f63809d5f40ba0fbdd0a6f35`。

[live]: https://github.com/cyjin-yl/TB371FC-6.12/blob/0d733edba701b45613670bc7d1e2c060db1a5e3d/docs/research/2026-11-24/evidence/native-dt/live-android.json
[backup]: https://github.com/cyjin-yl/TB371FC-6.12/blob/0d733edba701b45613670bc7d1e2c060db1a5e3d/docs/research/2026-11-24/evidence/native-dt/backup-final.json
[correlation]: https://github.com/cyjin-yl/TB371FC-6.12/blob/0d733edba701b45613670bc7d1e2c060db1a5e3d/docs/research/2026-11-24/evidence/native-dt/correlation.json
[libfdt]: https://github.com/cyjin-yl/TB371FC-6.12/blob/0d733edba701b45613670bc7d1e2c060db1a5e3d/docs/research/2026-11-24/evidence/native-dt/libfdt-final.json
[novatek-pr]: https://github.com/cyjin-yl/TB371FC-6.12/pull/9
[novatek-report]: https://github.com/cyjin-yl/TB371FC-6.12/blob/53a4c27a3fa1af401ec4f824343d8b044ad35929/docs/research/2026-11-24/novatek-wdt-history.md
