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
平板端发布的是有界摘要：wrapper 在裁剪字段前计算这四项完整 facts 的 canonical 哈希，并将哈希保留在摘要中。CT 端发布的是[完整安全元数据输出](ct-metadata.json)。比较器对 CT 的四项完整 facts 重算哈希，与平板摘要中的原始锚点比较；两份公开 JSON 的字段形状本来不同，不能把裁剪后的摘要重新哈希当成原始锚点。本次也从归档的 CT 元数据独立重算了两个哈希，结果一致。此比较证明指定静态解析结果一致，不能据此推断配置在启动时被选择、入口可达或 SMEM 已被初始化。

## 原始回执和方法

- [任务回执](job.result.json)：两个子阶段实际退出 0、未超时、stderr 为空，所属进程已回收。
- [安装回执](install-receipt.json)：执行源码、依赖、CT 语法检查和原文回读身份。
- [传输回执](transport-receipt.json)：整包校验工具正文，以及从偏移 1,380,352 起的 33 批成功回执、66 块的偏移与哈希。早先的数据由最终整包哈希覆盖；该文件没有声称记录所有失败的连接尝试。
- [传输完成检查点](transfer-checkpoint.json)：写于元数据解析启动前，原样保留 `overall_complete=false` 和 `metadata.state=not_started`。后续解包/任务回执记录接下来的成功阶段。
- [清单](manifest.json)：原字节大小与 SHA256；六份主要原始 JSON 和传输审计均已归档。
- [方法源码](method/)：本次实际执行的安装、解包、比较和监督脚本。源码中的私有输入路径属于这次执行环境。
- [先前平板元数据](../xbl-smem-static-20261123/metadata.json)和[元数据解析器](../xbl-smem-static-20261123/method/xbl_metadata.py)：保持原版本与哈希。

回执正文通过独立记录的文件大小和 SHA256 核对，再原样保存；没有补写失败阶段或替换历史结果。本目录不含归档、固件成员、分块正文或私有指令字节。这里的 `overall_complete` 只表示本次转存、解包与静态比较流程完成。
