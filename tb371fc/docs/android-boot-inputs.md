# Android 启动输入检查

本工具用于核对 Android boot / vendor_boot 镜像头的字段与文件布局。
[实际 CT 验证](../evidence/android-boot-header-20261123/ct-gate-01/README.md)
已通过九项自编样例测试。它为后续启动输入准备提供边界检查；
镜像头合法不代表镜像已经通过 AVB、能够启动或与平板硬件等价。

## 用法

在开发容器中执行，输出文件必须是新路径：

```sh
python3 -I -B -S tb371fc/tools/android-boot-header/test_android_boot_header.py

python3 -I -B -S tb371fc/tools/android-boot-header/inspect_android_boot_header.py \
  --image /work/device-assets/boot_a.img \
  --output /work/boot-header-observation-01.json
```

工具只接收普通文件，限制输入文件大小为 1 GiB，按已识别版本精确读取
对应长度的镜像头，重复读取核对稳定性。使用无缓冲读取，避免文件缓冲层
预读后续 payload。工具不会提取或执行镜像内容。

| 格式 | 处理范围 |
| --- | --- |
| Android boot v0、v1、v2 | 固定头长度、合法页大小、kernel/ramdisk/second 布局，以及 recovery DTBO 和 DTB 的边界 |
| Android boot v3、v4 | 4096 字节页、保留字段、kernel/ramdisk、v4 signature 区域；允许现代 init_boot 的 kernel size 为零 |
| vendor_boot v3、v4 | 头、vendor ramdisk、DTB，以及 v4 table/bootconfig 区域；核对 table 数量与记录长度 |

输出保留尺寸、偏移、版本和结构判断，不包含 cmdline、产品名称、
镜像 ID 或 payload。错误不被自动修补；未知版本或不一致布局返回失败。
v4 signature 区域的定位不等于签名验证；文件剩余尾部也不会被当成
已经验证的 AVB footer。

## 原生系统启动还需要的输入

现有备份清单已确认包含 A/B 两槽的 boot、recovery、vbmeta、dtbo，
以及独立保存的 GPU、DSP、触控和无线固件文件。该启动备份与
vendor 固件归档没有 system、vendor、product、odm 或 super
文件系统镜像成员。这个结论仅覆盖已核对的归档清单。

活动槽 boot 输入的提取与原生头检查正在单独推进。完整 Android
启动还需要匹配构建的逻辑分区镜像及相应启动配置，并须在实际
QEMU 硬件模型上完成用户态启动和外设验收。现阶段没有 Android
用户态启动成功或完整 TB371FC 模拟的验收结果。

## 格式来源

实现对照 AOSP 的固定格式定义和写入器；没有复制私有固件代码：

- [mkbootimg.py，固定提交 41681b2](https://android.googlesource.com/platform/system/tools/mkbootimg/+/41681b26562d133877d2e9d856685ee6c1d837d2/mkbootimg.py)
- [bootimg_priv.rs，固定提交 1628ef6](https://android.googlesource.com/platform/system/tools/mkbootimg/+/1628ef64f6527293bbd4f4b4c1e44f2dc3534db3/rust/bootimg_priv.rs)

解析器只声称其已实现和测试的字段范围。它没有给平板当前分区备份
赋予“原厂未修改镜像”的认证。
