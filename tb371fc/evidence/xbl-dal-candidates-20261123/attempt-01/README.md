# XBL DAL 格式候选解析 — 第一次实际运行

**34 项合成解析测试实际通过；本机 XBL 扫描实际完成，接受的配置候选为 0。** 该结果没有给出 `smem_vers`、`smem_toc_vers`、`smem_partitions` 或 `smem_max_items` 的可信值，也没有生成 SMEM handoff。

## 实际执行

执行环境是 doesvm CT102 的 `qemu-tb371fc-dev`，独立目录为
`/work/xbl-dal-candidates-20261123-01`。只读输入来自[已经完成整包核验的 XBL 备份](../../xbl-transfer-completion-20261123/README.md)，大小 3,670,016 字节，SHA256 为
`eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55`。

| 阶段 | 实际结果 |
| --- | --- |
| 合成 fixtures | 34 / 34，退出 0，原始日志保留 |
| 固定 XBL 静态扫描 | 退出 0，完成有界扫描 |
| 检查的 8 字节对齐位置 | 21,481 |
| 几何上包含已知 descriptor 的候选 | 184 |
| 超出设备数量界限 | 183 |
| 进入完整候选验证 | 1 |
| 完整接受 | 0 |
| 剩余拒绝原因 | `property_name_offset` |
| 候选处理截断 | 无 |

已知 descriptor 的映射、名称指针和 DJB2 检查通过后，程序才会进入候选扫描。唯一有界候选在属性名相对偏移检查处被拒绝。v1 没有记录该拒绝发生于局部列表还是全局列表，也没有导出部分属性值；下一版本仅增加该拒绝点的数字结构上下文，保留这次结果和全部拒绝条件。

[实际任务回执](job.result.json)记录两个子进程、退出状态、超时状态及输入/六个源码文件的前后 SHA256。两阶段均未超时，所属进程已回收；扫描 stderr 为空。原始 `job.exit=0` 与 `overall_complete=true` 表示这次测试和静态扫描流程结束，并不表示接受了配置。

## 可核对的原件

- [34 项合成测试日志](fixtures.stderr)
- [实际零候选扫描输出](scan.stdout)
- [原始任务回执](job.result.json)、[退出文件](job.exit)和[安装回执](install-receipt.json)
- [身份清单](manifest.json)和[本次执行的方法源码](method/)

`method/METHOD.md` 是执行前冻结的说明，原字节保留，其中的“尚未执行”描述其编写时状态；实际结果以上方原始回执为准。清单的 `git_tree_or_ref_changed=false` 描述采集阶段，随后本归档提交才将其挂接到仓库。

解析器依据固定 SDM670 源码镜像提出一种格式候选，既没有假定这是经 OEM 验证的 Kona 实现，也没有复制其他平台的 SMEM 默认配置。测试数据全部在内存中自编，34 个通过项不代表 34 项真实硬件行为。固件执行、原厂平台选择、XBL 入口可达性、SMEM 初始化和 Android 启动均未由本次运行证明。
