# Original-input diagnostic for qualified DSI runtime 8e8e19a

The exact qualified binary was executed once on 2026-11-24, with the unchanged original kernel, ramdisk, complete DT, bootargs, AOP and symbols. Both DSI PLL drivers registered. The next first fatal access is DP PLL base +0x158 at guest 6.260019 s, followed by PID1 panic. The corrected runner captured the failure and completed normal QMP shutdown. The source result and raw logs remain hash-bound in the isolated run output.

This diagnostic attached zero original LUNs. Android userspace, UI and boot completion are false/unobserved. The source result digest refers to the original 6,915-byte JSON; the embedded decoded object is not claimed byte-identical serialization.

The prior exact-head CI and independent audits remain in ../ci-8e8e19a. This evidence commit does not change the tested runtime identity.
