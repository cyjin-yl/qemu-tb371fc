# TB371FC 整机模拟：实现与验收路线

完整模拟的目标是让真实来宾驱动面对尽可能一致的软件可见设备接口。
QEMU TCG 的执行速度不代表真实 CPU/GPU 性能；功耗、射频、电气行为和周期精确性
需要另外的模型与实验，不能由功能模拟的通过结果推出。

所有条目都要求：可追溯的硬件/源码依据、可检查的实现、自动化来宾验证，以及明确的
实机对照范围。实现、模拟测试通过、真机对照通过是不同状态。

## 1. 板级启动基础

- [x] 新增独立 machine，固定上游基线，单独开发容器。
- [x] 通过实机只读采集核对型号、MPIDR、RAM、GIC、timer 与启用的 UART。
- [x] 实现 CPU/GIC/timer/PSCI/RAM 装配与生成的裁剪 DT。
- [x] 实现有状态的 GENI FIFO 串口与针对寄存器行为的检查。
- [ ] 运行 QEMU 与实机 profile 对照并保存回执。
- [ ] 以明确来源的 Linux Image 启动 initramfs、八核在线并正常退出。
- [ ] CPU 精确功能特征模型：核对 Cortex-A77/Kryo 的架构寄存器、缓存描述与扩展。
- [ ] 用同一份非破坏性探针在模拟器和实机上对照明确允许读取的接口。

## 2. Qualcomm 资源、时钟和固件契约

- [ ] SMEM、SoC identity、共享内存分区和版本结构。
- [ ] 最小 SCM/SMCCC 服务；每个支持的调用有参数、返回值及错误路径测试。
- [ ] Kona cmd-db、RPMh RSC/TCS、GCC/GDSC/reset；不移植其他 SoC 的资源表后冒充。
- [ ] TLMM、pinctrl、GPIO 中断和 PDC/wakeup 路由。
- [ ] PSCI off/on/reset、热管理及 watchdog 的驱动可见行为。

验收：使用同一主线设备树和真实驱动完成初始化，验证失败与取消路径，
将合成固定时钟替换为有状态的资源模型。合成固件服务仍须在输出中明确标识。

## 3. 存储、USB 与输入

- [ ] SM8250 SMMU-500/SMMUv2，地址翻译、fault、TLB invalidation 和 context bank。
- [ ] UFS HCI、Qualcomm wrapper/PHY、队列、复位和块设备错误；通用 PCI UFS 不能替代这些接口。
- [ ] SDHCI/microSD 和 DWC3/USB PHY。
- [ ] GENI I²C/SPI、真实设备寻址、IRQ 与读写顺序。
- [ ] NT36532 触摸/笔协议、键盘盖 I²C-HID、attach/detach/wakeup。
- [ ] CTN730 笔充电、MM8013 电量计、PMIC/charger 的行为模型。

验收：存储校验和、故障注入、输入报告回放与设备生命周期均可复现。
实机输入对照使用专门测试动作，不录制日常输入。

## 4. 显示与 Adreno 650

- [ ] 先建立显式声明为显示辅助的 framebuffer 输出，独立于 GPU 成功结论。
- [ ] SM8250 DPU、双 DSI、DSC、面板初始化和模式切换。
- [ ] A650 GMU/HFI、寄存器、ringbuffer、命令包、interrupt/fence 与错误恢复。
- [ ] A650 内存访问通过真实 SMMUv2 契约，并核对同步/缓存行为。
- [ ] 实际命令执行和渲染；比较固定测试图像与允许误差。

验收：先 driver probe，再提交/完成/fault，再图像结果。
固定成功 ACK、virtio-gpu、主机 GPU 转发或其他 Adreno 世代的设备身份均不能作为
“A650 硬件模拟已完成”的证据。

## 5. 连接、音频与其余协处理器

- [ ] QCA6390 PCIe/MHI、Wi-Fi/BT 驱动和固件服务；射频环境另行抽象。
- [ ] ADSP/CDSP、remoteproc/GLINK/RPMsg/QRTR 所需契约。
- [ ] 音频 ASoC/SoundWire/TFA9894/WCD938x、路由与时钟。
- [ ] 相机、传感器、剩余 PMIC 与板级器件。
- [ ] 挂起/恢复、热状态和跨设备低功耗依赖。

## 6. 原厂软件与完整性验收

- [ ] 在许可证允许的本地输入下，解析原厂 boot image、DTB/DTBO 和内核参数。
- [ ] 原厂内核实际引导，不用替换内核或删去必要设备隐藏兼容性问题。
- [ ] Android userspace、显示、输入、存储和网络的端到端任务。
- [ ] reset、snapshot/migration、错误注入和重复启动。
- [ ] 逐设备覆盖清单、仍合成的服务、差分结果及可复现实验归档。

只有上述依赖与验收逐项具备证据后，才适合讨论整机兼容性。
单次成功启动、地址一致、驱动探测通过分别是有用进展，不等同于这个最终门槛。
