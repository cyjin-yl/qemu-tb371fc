# Generation2 离线续传准备（审查草案）

## 归档范围与运行位置

本目录保存已审查的核心和合成测试，供后续生产接线复用；它没有生产设备入口，也没有加入自动采集流程。`resume_core.py` 与原审查候选逐字相同。归档时只将测试脚本固定的历史目录改为必须显式提供的 `TB371FC_RESUME_HISTORY`，并重新运行全部九项测试。

测试依赖该用户已经保留的、SHA 固定的 collector、generation2 plan 和 producer；这些私人历史输入不随代码公开。将 `TB371FC_RESUME_HISTORY` 指向包含 `qemu-runner-shutdown-work/` 和 `generation2-capture-work/` 的历史目录后执行：

```bash
python3 test_resume_core.py
```

缺少环境变量或任何历史文件不匹配固定 SHA 时停止，不自动寻找、下载或重写这些输入。归档版本的运行位置由上述环境变量指定。公开的 `validation.json` 记录实际归档重跑结果和原独立审查收据哈希，不包含这些私人历史输入。

原始离线审查实现了续传核心，并用新建的小型普通文件验证。**没有连接平板，没有采集真实块设备，没有运行 assembly，也没有修改冻结计划。**

它不是现成的生产启动器：没有生产 SSH/设备连接入口，没有自动寻找设备，没有接入全部历史分段的锁定字节审核，也没有接入 generation2 最终完成与 assembly 发布。`fixture_receiver.py` 明确拒绝块设备来源，仅接受带专用标记的新建普通文件 fixture。

## 先决事实与范围

历史清点记录为 82 个完整分段，即 prefix 与 tail-000 至 tail-080；tail-081 有六个带 receipt 的完整块和第七块的未提交 partial。后续为 tail-082 至 tail-105。这里没有远端重查，因此这组数量是恢复草案的**待重核输入**。如果现场不再符合它，草案会停止，不能自动套用旧库存。

| 冻结约束 | 值 |
| --- | ---: |
| 新 generation2 jobs | 107 |
| 已完成历史 jobs（待现场重新审核） | 82 |
| 严格恢复 job | tail-081，16 块，其中 6 块待 live 重读 |
| 仅在目标不存在时允许的新 jobs | tail-082 至 tail-105，共 24 个 |
| generation2 新块总数 | 1794 |
| 保留 super 块数 | 48 |
| assembly manifests | 108 |
| assembly 必须验证的总块数 | 1842 |

82 个已完成段只保持“不同时间的历史读取”性质。它们不会因为恢复继续而变成同一时刻的 snapshot。`snapshot_consistent`、`source_immutable_claim_verified`、`pristine_factory_images`、`restore_tested`、`android_boot_tested` 始终为 false；本核心不发布 `all_backups_complete`。

## 核对的精确原件

| 原件 | SHA-256 |
| --- | --- |
| 冻结 generation2 plan | `a90c1161c6cbce7d0fada8e1901354f0a58b5707e02231d206a0c1f663a1a77a` |
| 原 generation2 producer | `cff183ad6a78024e83aa3e9fcc188d519a168d3bf51a92467f9dca5eee233587` |
| 原 collector | `eb1258cb2604cfd99540b2320b28bde0a14284d940258b4f95bff3980c0595be` |
| 原 generation2 assembler | `19b6f1c42ee5e7e4515a3ff399c21185b88ce28b6d06c84c9c31f32af44f37b8` |

读取位置均在显式提供的历史目录中。producer/plan/assembler 位于 `generation2-capture-work`；精确 collector 位于 `qemu-runner-shutdown-work/tb371fc/scripts/acquire_block_ranges.py`。测试运行前后复核 plan、producer、collector 的哈希，确认未修改。

原 producer 是一次性 fresh 程序：初始化要求 generation2 根目录不存在，逐 job 调用 `cap.receive(..., resume=False)`。它没有续传入口，不能直接再次执行来恢复现有 generation2。

原 collector 的严格 resume 有两个关键行为：

1. 接收端先锁定 `capture.lock`，验证已有 receipt 和压缩文件，但在发出 `ready/completed` 握手前会清理未提交文件。包括顶层 plan/manifest partial、有 receipt 的 receipt partial，以及没有 receipt 的 chunk 目录里的 `data.gz.partial`、`data.gz`、receipt partial。
2. 发送端拿到接收端已完成块列表后，对每个已有块用当前源 fd 执行 `hash_range()`。仅在与历史 `source_sha256` 一致时才发送 `skip`。任何不一致立即抛出 `source changed since completed chunk was captured`。新块仍走 before/stream/after 三次源观察一致性检查。

因此，接收端“磁盘上 gzip 与 receipt 一致”不能替代源端 live 重读；也不能在未保全 partial 时先启动原 receive。

## 最小恢复核心如何工作

`resume_core.py` 只加载 SHA 固定的 collector，保持其文件、sender、wire protocol、manifest schema 和 resume 验证逻辑不变。提供的接收包装器仅用于 `resume=True`，没有 fresh fallback 分支。

接收包装器先读取并严格比较 incoming plan；source identity、ranges、geometry、chunk size、模式与绑定的 expected plan 必须逐项一致。随后按以下顺序工作：

1. 以不带 `O_CREAT` 的方式打开现存 `capture.lock`，要求它是该用户私有的普通文件、link count 为 1，并对原 inode 取独占 nonblocking flock。缺失锁、已占用锁、symlink、异常条目都停止。
2. 在锁内复核 committed plan、六个已有 receipt 与对应 gzip 的压缩哈希、解压长度和源哈希。已完成块集合必须正好是 `[0, 1, 2, 3, 4, 5]`。
3. 将所有可能被原 collector 清理的未提交文件复制到独立、私有、此前不存在的保全目录。使用独立文件复制而不是共享 inode 的 hardlink。逐文件核验稳定 stat、长度、复制哈希及目的文件重读哈希，fsync 文件。
4. **从最深目录到保全根目录同步整条新目录链**，再独占创建并同步 `preservation.json`。这一步包括中间 `files/` 目录；仅同步叶目录不足以承诺掉电持久性。
5. 把原 collector 本次打开 `capture.lock` 的请求映射为外层已持锁 fd 的 `dup`。两者共享同一个 open-file-description。原 collector 仍执行真实 `flock(LOCK_EX | LOCK_NB)`；没有拦截或跳过任何 flock/LOCK_UN。
6. 调用原 `receive(..., resume=True)`。原 sender 必须对六个现有块重新 live hash，之后才可 skip。接收端保全记录明确写着 `live_source_reread_performed_by_preservation: false`，不会把本地文件核验冒充 live 源证明。
7. 原 receive 返回后，只读检查 canonical 文件与锁 inode；后续记录只写到独立保全目录。最后关闭外层 fd。

### 为什么没有锁交接间隙

精确 `eb1258…` 的 `receive` 只有获取锁的 flock 调用。其 `finally` **只调用 `os.close(lock_fd)`，没有 `LOCK_UN`**。因此它关闭 duplicate fd 后，外层持有的同一 open-file-description 仍保持锁定，直到外层自己关闭。

`_DupLockOS` 只是该新加载 collector 模块内部的 `os` facade，专门处理这一次、这个路径和这组固定 flags 的 open。其他调用原样委托系统 `os`；`fcntl` 模块完全不变。不同 receiver 调用必须分别加载 collector 模块，不能共享一个正在适配的 module 对象。

这项结论只适用于已核对的原件。若未来 collector 改成显式 `LOCK_UN`，则 duplicate 会共同失锁，必须重新设计/审查；当前哈希 pin 会先拒绝新版本。本草案不能用“外层 fd 还开着”代替实际锁证据。

### 原 partial 路径与保全字节的区别

原 collector 在接收端握手前可能移除 canonical partial 路径。这份包装器保证它在清理前已有经过校验并同步的独立副本，**不承诺旧 partial 路径在原 receive 开始后始终存在**。若 live 源变化导致 sender 退出，六个 committed gzip/receipt 保持原文；原 partial 字节保留在独立 archive，失败结果不会启动 fresh capture。

如果后续部署要求“发生源变化时，canonical 目录连 partial 路径都必须逐路径完全不变”，当前草案不满足这个更强约束，需要额外的源端预检或另行审查 receiver 设计，不能把本实现直接当作该保证。

## 错误与 barrier

`run_pending_once()` 是依赖注入的协调器骨架。每个待运行 job 前后核对 live identity 回调；任何回调、sender、receiver、保全、锁或 acknowledgement 异常直接退出循环。没有捕获错误后换成 fresh 的代码，没有自动修改源绑定或删除/重建旧目标的代码。source identity 对不上时连 sender 都不会被调用。

实际生产的 identity 回调尚未连接。它将来必须按冻结 source-binding 校验真实只读块设备的完整 identity，不能只比较设备名称、容量或型号；也不能在 inode/rdev 等变化后自动改写 binding。

`narrow_recovery_schedule()` 只接受已审核的 82/1/24 布局。未来 job 的“fresh”是它原本完全不存在的目标，不是失败的 tail-081 被重新创建。发现任何已存在的未来目标都要求重新审查库存。

`count_barriers_only()` 只校验 job 顺序、数量及块数，明确返回 `byte_verification_performed_by_this_check: false`。它不写 `generation2-complete.json`，不写 `ready.json`，不启动 assembly。它不替代原 assembler 对 108 个 manifests、1842 块及六 LUN 输出的实际字节验证。

## 合成验证结果

候选核心 SHA-256：`812a0fdd379cd054889276d5b0c33f9c6ff808cb88d938e9bd26e61aa8351c7c`。四个归档代码文件的冻结哈希见 `validation.json`。

本地与独立审查者分别运行 **9/9 通过**，测试只使用新建的 64 KiB 普通源文件、16 个 4 KiB 显式范围；collector 的 chunk size 参数仍为 64 MiB。没有修改真实 generation2 plan 来缩小测试。

| 验证 | 观察结果 |
| --- | --- |
| 同源严格恢复 | 原 sender 对已有六块各 live hash 一次；manifest 的 resume 索引为 0–5；原六份 gzip/receipt 逐字未变 |
| 第六块源内容变化 | 原 sender 读到第六块即失败，后续 fresh 回调未调用；六份历史文件和 partial 保全副本保持一致 |
| 独立进程探锁 | 保全、原 receive 重入锁后、原 receive 返回后等 7 个关键时点均无法 nonblocking 抢到原 inode；外层关闭后才能取得 |
| 已有锁被占用 | 不创建保全目录，不清理 partial，不进入 collector |
| 源 identity 不符 | sender 从未被调用 |
| partial 为 symlink | 停止且不读取/清理指向的证据文件 |
| 中间目录 fsync 失败 | 原 receive 未进入，canonical partial 未清理，未生成可授权清理的 preservation receipt |
| 父目录同步顺序 | 叶目录、`files/`、保全根目录的必要同步先于 collector-entry |
| 原锁路径被替换 | 在 collector 清理前检测到 inode 变化并停止；合成测试保留两份锁文件 |
| 冻结计数 | 107/108/1842 通过；少 job、少 super 块、未来目标已存在均拒绝；原 plan 哈希未变 |

源变化时，receiver 会看到 sender 关闭传输后的 EOF（原错误文本为 `truncated/oversized protocol JSON`）；source 端的真实 `source changed…` 错误另存于该 fixture 的 `sender-stopped.json`。不要把这个预期 receiver EOF 误解为测试通过了真实设备恢复。

本地验证 receipt：

`test-artifacts/20261125T004432Z-d3927ef2/test-receipt.json`

独立验证 receipt 单独保留，其 SHA-256 为 `82f835b7d2716b181d7c9b09e6cd3c756681ff98cc5098441e0180901e75196f`。此摘要绑定原始记录，不是公开下载位置。

所有测试产物和失败现场均保留。测试没有执行目录清理；原 collector 对合成 partial 的正常清理发生在对应保全副本已提交之后。重跑测试会创建新的时间戳/UUID 目录，不复用或删除此前 fixture：

```bash
python3 test_resume_core.py
```

## 部署前仍需完成的具体工作

这些是尚未实现/验证的接线，不是本次任务中的设备操作：

1. 从真实 source-binding、保留 super manifest 和冻结 plan 读取并校验精确文件哈希；在明确绑定的同一源上核对完整 live identity。此离线任务没有读取真实设备。
2. 用原 `capture.lock` 的共享锁和已有 byte verifier 重核 82 个历史分段；对 tail-081 取独占锁后重核精确六块库存。不能直接把传入 `narrow_recovery_schedule()` 的数量当作已经完成字节审核。
3. 接入经过审核、显式提供的 source/receiver transport，并 pin 新包装器自身的 hash。原 collector、job plan 和 source binding 不变。源端继续使用原单写者锁与 active-producer 排他检查，保留锁 fd 的生命周期。
4. 为 tail-081 调用本 strict-resume 包装器。只有它完成且 sender 在子进程退出码为零后返回完整 acknowledgement，才考虑后续不存在的 targets。后续 fresh 接收端也必须执行 frozen job/source/target 绑定验证，不允许 fallback。
5. 以实际 manifests 和已验证的数据重新检查全部 107 jobs / 108 manifests / 1842 chunks。真正的 generation completion 仍需与原 assembler 的精确 receipt 契约对接；本草案不发布 completion 或 ready。
6. 在真实文件系统上核对保全位置容量、私有权限、同 inode 锁协作和 fsync 支持。本次用独立进程与同步故障注入验证程序顺序，没有做真实掉电或生产传输试验。

原 receiver wrapper/collector 的现成 resume 命令在完成上述接线前不能直接启动，因为它会先清理未提交文件。这里没有给出绕过审核的生产启动命令。
