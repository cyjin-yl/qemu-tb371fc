# XBL 转存与静态交叉核对 — 2026-11-23

**doesvm CT102 已完成整包验证、精确解包和两份静态元数据的双端一致性检查，实际 job.exit 为 0。** 此目录记录这一已执行阶段；完整原生设备树启动、Android 启动及原厂 SMEM 初始化仍未通过。

## 已取得的结果

完整传输文件为 **2,849,209 字节**，SHA256 为
`30a30d8670bd69579d2a250ddcc542b99f3140d062a8b95cb61c09bfd8f1671e`。
平板端原捕获发生于 10:43:53–10:43:54 UTC；doesvm 完成任务结束于 **13:48:50 UTC**。文件来自当时活动槽的只读备份；这里没有将其认定为未经修改的出厂镜像。

| 精确成员 | 字节数 | SHA256 |
| --- | ---: | --- |
| xbl_a.bin | 3,670,016 | eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55 |
| xbl_config_a.bin | 131,072 | bb16d00d0f7581caa641d2f1c736adf5cc6505403c67250c47e8a893cbda93ec |
| capture.json | 1,943 | 610c04eb3ef5dc5599721a778d6fba8b1804e154f20672b11dd5a60fb6d75f0a |

解包器核对三个普通 USTAR 成员、各自大小与内容哈希，以及整包身份；拒绝链接、重复成员和不符合预期的归档。三个输出均以 0600 新建，传输文件与暂存文件保留。详见[原始解包回执](completion-receipt.json)。

两端使用同一元数据解析器。比较器仅对 `elf`、`firmware_volumes`、`allowlisted_markers` 和 `memory_map_literal_candidates` 四个字段作规范化，再比较 SHA256：

| 输入 | 平板端与 CT 端共同的 canonical SHA256 |
| --- | --- |
| XBL | 255aaf0cb2718ac10e81bd0bb11a0a98ea4e31cfa2bc9addec51b0966e2a4789 |
| xbl_config | eb568b572d79d80a2ab3e1a2377ddb9efc6f33211205326fcba68071e07a7d3f |

[原始比较回执](canonical-comparison.json)记录两项实际 `equal=true`。
文件路径与执行时间等环境字段不属于这四项，因此两端完整 metadata.json 的文件哈希不同。此比较证明指定静态解析结果一致，不能据此推断配置在启动时被选择、入口可达或 SMEM 已被初始化。

## 原始回执和方法

- [任务回执](job.result.json)：两个子阶段实际退出 0、未超时、stderr 为空，所属进程已回收。
- [安装回执](install-receipt.json)：执行源码、依赖、CT 语法检查和原文回读身份。
- [传输回执](transport-receipt.json)：整包校验工具正文，以及从偏移 1,380,352 起的 33 批成功回执、66 块的偏移与哈希。早先的数据由最终整包哈希覆盖；该文件没有声称记录所有失败的连接尝试。
- [清单](manifest.json)：原字节大小与 SHA256，以及当前尚未归档全文的两份附属结果。
- [方法源码](method/)：本次实际执行的安装、解包、比较和监督脚本。源码中的私有输入路径属于这次执行环境。
- [先前平板元数据](../xbl-smem-static-20261123/metadata.json)和[元数据解析器](../xbl-smem-static-20261123/method/xbl_metadata.py)：保持原版本与哈希。

回执正文通过独立记录的文件大小和 SHA256 核对，再原样保存；没有补写失败阶段或替换历史结果。本目录不含归档、固件成员、分块正文或私有指令字节。这里的 `overall_complete` 只表示本次转存、解包与静态比较流程完成。
