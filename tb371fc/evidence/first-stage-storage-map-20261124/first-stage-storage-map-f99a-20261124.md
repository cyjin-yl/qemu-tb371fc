# First-stage 存储依赖映射（私有协调，2026-11-24）

结论：旧 boot_a ramdisk 的 15 条 active fstab entry 中，有 5 条 slotselect logical 和 1 条 /metadata 首阶段挂载。按本轮明确选择的 _a，五个逻辑名均匹配新 super metadata slot 0；它们的物理容器是 LUN 0 / sda 的 GPT 第 6 项 super。这里只证明名称与几何对应，不证明这些分区内容可以启动。

## 输入与 hash 绑定

- fstab capture：boot-fstab-and-sbm-symbols-b95f97.json，15,552 B，SHA-256 35d5058109f340dbec4cbaa7b31dcc57475e3335af410641c381300064d4e3b5。
- 捕获的 fstab.qcom：5,171 B，SHA-256 ef90833a01ebb5ca8d1c03d047a6f957f14e51ff4278bd06cd653dc29cf1960e；15 条 active、6 条 first_stage_mount、5 条 logical，archive 中匹配文件 1 个。未重读原 fstab。
- 该 fstab 来自 2026-11-23T19:54:20.524879Z 的固定 ramdisk；slice SHA 563f4b334f8470155c6a451395342f7044b70e79b480a886c7641960f534f596；解压 cpio SHA db7f63f0ccefd8d2320142b806d42ade4c6a2edaa3dc9f1ca6295d78e0f8e542。
- 旧 boot_a 全 image SHA 78ba0f4f75bec85a80155a9e9ea8fa19546bbd88598a7923a16b069404899a9a 是 capture 引用的 prior root receipt；collector 和本次审阅都没有重 hash 全 image。旧 stat 大小 100,663,296 B 与新 GPT boot_a 大小相同，大小相同不证明内容相同。
- 新 super attempt003 report SHA b299ff0af1885e66759785534d91c5df296b3cd2ac1409e0b07b0485d5f65ada；prefix SHA 6bf1fbcdb6e63a6208aebd088cbe11ad0c6040fab6c3ec45fc15e547623e551c。slot 0、8 partition records、20 extents；其他两个 metadata slot 未验证。六份 GPT report hash 见相邻 JSON。

_a 是本轮原 boot_a/slot0 工作选择，不是从 GPT 属性推断的设备当前 active slot；未读取 bootargs。旧 fstab 与新 GPT/super 是跨时点观察，不构成原子或一致的完整备份。

## 五个首阶段 logical mount

| fstab 行 / mount | slotselect 名称 | LP 字节数 / extents | 明确的 avb label | COW metadata record |
|---|---|---:|---|---|
| 38 / /system | system → system_a | 6,160,855,040 / 3 | vbmeta_system_a | system_a-cow |
| 39 / /system_ext | system_ext → system_ext_a | 516,452,352 / 5 | vbmeta_system_a | 未见对应 record；不据此排除 snapshot |
| 40 / /product | product → product_a | 608,817,152 / 4 | vbmeta_system_a | product_a-cow |
| 41 / /vendor | vendor → vendor_a | 804,339,712 / 4 | 只有 avb flag，无明确 label | 未见对应 record；不据此排除 snapshot |
| 42 / /odm | odm → odm_a | 1,097,728 / 1 | 只有 avb flag，无明确 label | odm_a-cow |

五项都配置 ext4、ro、wait、slotselect、logical、first_stage_mount、avb，LP group 均为 qti_dynamic_partitions_a，attributes=5（READONLY | UPDATED）。system 另有 avb_keys flag，但 key 参数值已脱敏，未打开任何 key 路径。

这些 *_a 是 super 中的 LP 名称，不是额外 GPT 分区。GPT LBA 单位为 4096 B；LP extent target_sector/num_sectors 固定为 512 B。若只作地址换算：LUN 内偏移 = super GPT offset（35,684,352 B）+ LP target_sector × 512；这不是运行中的 snapshot/dm 映射。

## 实际 GPT 位置

| label | LUN / sdX / GPT entry | LUN 内字节偏移 | 字节数 | 用途/边界 |
|---|---|---:|---:|---|
| super | 0 / sda / #6 | 35,684,352 | 12,884,901,888 | 5 base + 3 COW 的 LP 物理容器；sda6 与 super capture receipt 一致 |
| metadata | 0 / sda / #11 | 13,130,432,512 | 16,777,216 | fstab 行43，ext4，first_stage_mount；无 slotselect |
| vbmeta_system_a | 0 / sda / #9 | 13,130,301,440 | 65,536 | system/system_ext/product 的明确同slot avb label |
| vbmeta_system_b | 0 / sda / #10 | 13,130,366,976 | 65,536 | 另一个已存在GPT label；未验证 _b LP slot |
| vbmeta_a | 4 / sde / #16 | 636,723,200 | 65,536 | 同slot AVB 根候选；未读取验证链 |
| vbmeta_b | 4 / sde / #39 | 1,336,598,528 | 65,536 | 另一个已存在GPT label；未验证 _b 链 |
| userdata | 0 / sda / #15 | 13,549,862,912 | 112,170,184,704 | fstab 行44，f2fs latemount；非 first_stage_mount |
| boot_a | 4 / sde / #11 | 534,798,336 | 100,663,296 | 旧 fstab 来源标签；这里只匹配大小，不证明当前内容身份 |

按标准 GPT entry 编号可得到 sda6/sda9/sda11/sda15/sde11/sde16 等预期内核分区节点；除既有 super capture 的 sda6 记录外，本审阅没有另查这些节点或 by-name symlink。JSON 以 LUN、GPT entry、label、offset/size 为身份。

## 未获得的资格

- AVB：vbmeta_system_a 的 GPT 位置已定位；vendor_a/odm_a 的 fstab 只有 avb flag。vbmeta_a 仅是所选 _a 下已存在的根候选，不能据此把实际 descriptor 链归给它。未读 vbmeta payload、keys、rollback metadata，也未认证 AVB。
- COW/snapshot：odm_a-cow 135,168 B、product_a-cow 12,013,568 B、system_a-cow 4,753,719,296 B，总计 4,765,868,032 B。这些 record、Virtual A/B header flag 和 UPDATED 属性不证明 OTA 活跃或 merge 状态。未读 /metadata/ota、snapuserd/dm 状态、COW payload；不能把 base extent 直出称为完整有效运行映射，也不应按此报告丢弃 COW。
- /metadata：fstab 的 formattable 是配置，不是授权格式化。该分区可定位不证明文件系统完整、可挂载或 snapshot state 一致。
- /data：仅记录 f2fs、latemount、fileencryption、metadata_encryption、keydirectory、inlinecrypt，以及原 metadata 中的其他 flag 名称。参数值已脱敏；未读 userdata/key material。key 可移植性、解密能力和 mountability 均为 unknown，不作能或不能的推断。
- 其他 fstab 非首阶段分区：persist=LUN0#2，misc=LUN0#3；modem_a=LUN4#4、bluetooth_a=LUN4#5、dsp_a=LUN4#9。外部存储 pattern 在原 capture 中已脱敏，未尝试解析。
- 本表是依赖子集，不是完整 storage manifest；不授权省略其他 LUN，也没有获得完整 raw-LUN backup、atomic snapshot、filesystem integrity、Android first-stage 或启动资格。

本次仅对已有 JSON metadata 做一次性对应，未建立 generic parser，未访问设备、分区 payload、raw fstab 或 boot image，未修改任何 owner 文件。
