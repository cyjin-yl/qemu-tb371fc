# Actual RAS profile native run: independent evidence review

The diagnostic run of commit `00d2a46bc994d33f484715d81b201a2cfb9b7018` ended with native and outer exit code **2**. It did not establish Android user space, completed boot, a working UI, or complete storage. QEMU itself shut down normally with process return code **0** after the diagnostic time bound. These are different outcomes.

The first identified kernel failure is an **undefined instruction at guest time 5.406881 seconds**, in CPU 0's `cpuhp/0` task (PID 18). The complete QEMU trace records a read of `S3_0_C15_C5_0`, immediately followed by an Undefined Instruction exception from EL1 to EL1 and ELR `0xffffff8008d17b1c`. The separately pinned original-consumer analysis maps that address to `dsu_pmu_cpu_online+0x6c`, within its complete 280-byte function. This review checked that address and report linkage; it did not repeat the function's binary disassembly.

The following Data Abort is a separate exception: its FAR is `0xfffffffffffffff0` and its ELR is `0xffffff8008093d00`. It must not replace the preceding unsupported system-register read as the initial observed failure. The first kernel diagnostic prints null PC and LR, so the initial instruction address comes from the full QEMU exception trace, not from inventing a usable kernel stack frame.

## Complete evidence and identity

The reviewed artifact is **15089028973**, from push run **45758685765**. Its archive is 87,595,876 bytes with SHA-256 `e0fe3fb087d863448d94061a0d6caecb5d565ee93a80bebcba06875cfb2e9a32`; the QEMU executable is 63,969,112 bytes with SHA-256 `09d6a18d291ec460092561feaf121282215a614b443a394eb3cfd7e12d500964`. No artifact or native entry was executed by this review.

The independent collection parser reconstructed all **39 frames**, the complete 1,169,284-byte compressed payload, and the complete 10,241,168-byte manifest. The 81 manifest rows comprise **63 complete files totaling 7,657,152 bytes**, **16 inventory-only binary observations**, and **2 explicitly absent files**. Every complete local file was compared byte for byte with its manifest content and its SHA-256. Inventory-only observations were not converted into invented local files.

The original collection namespace-limit failure remains preserved. A later disconnection at offset 750000 also remains a failure in its own original record; its explicit sequence-002 recovery is linked to the same command, the preceding 25 frames, its complete invocation and actual successful return. The independent review checked that chain without issuing another collection request.

The complete four-file deployment and dispatch records, source identity, actual execution binding, and qualification digest agree. Reversing only the two permitted coordinator literals reproduces the frozen source byte for byte; all 44 definitions remain unchanged. The launcher has the corresponding complete inverse proof. The qualification retains the actual failed registration reader and its subsequent independent RAS recovery; this review does not fabricate one uninterrupted successful reader sequence or rerun the prior CI auditors.

## Pure observer, native protocol, and input scope

The pure preflight has a complete outgoing journal of **182 QMP requests** and **184 incoming messages**. The review checked all **176 typed getter replies**: 140 QCE, 4 NPU, and 32 RAS values. Both passes agree with the frozen profile. The NPU profile is 2, and all eight CPU RAS profiles are 1. There is no `cont`, QTest, MMIO, state mutation, or original kernel attachment in this phase. These are configuration observations, not proof that the original guest has successfully used every modeled system register.

The native phase has **8 complete printk captures**, **86 consecutive response IDs**, and **104 incoming QMP messages**, including 17 events. Every capture's stopped state, scalar memory observations, complete metadata and log digest agree. Ring-buffer digests are bound to the inventory, but the raw rings were not retrieved; a binary ring-decode replay is therefore not claimed. The native command order is reconstructed from the complete received replies and the pinned runner. There is no independent outgoing native command journal.

Both phases have a complete quit acknowledgement, a normal host-requested shutdown event, a closed QMP drain with zero residual bytes, and QEMU process exit 0. The pure observer has an explicit wait receipt. For native execution, the exact runner source places `wait(timeout=3)` before recording return code and the received-message file; there is no separate native wait-count receipt. The outer completion record and terminal exit are checked independently.

The actual selection was a **no-LUN diagnostic** because the generation-2 readiness record was absent at selection time. No original LUN, overlay, or full-LUN binding was attached. The existing v1 selector was used; a future mapped-v2 handoff was not exercised. The original kernel, ramdisk, device tree, kernel argument text, AOP-derived database, and symbols retain their recorded hashes and preservation results. This is evidence from the pinned runner and coordinator; their original payloads were not retrieved again for this review.

## Original classifier limitation

The frozen runner's fatal pattern omitted undefined-instruction diagnostics. Its unchanged result therefore says `time-bound`, reports no first kernel failure, and has outcome `no-android-userspace-evidence`, even though the first snapshot already contains the exception. The independent full-log parser identifies and deduplicates that earlier failure across all eight snapshots. It agrees with the later local retrospective analysis. Neither analysis rewrites the original result or represents a new native run.

The public JSON contains only selected scalar facts, hashes, relative evidence labels, and allowed complete log lines. All three public files are scanned in full UTF-8 for prohibited content before delivery. Private raw records, command arrays, binary instruction bytes, and host locations are not included.
