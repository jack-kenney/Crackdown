// Crackdown TU0 pacing experiment. Deliberately excluded from the normal build.
// This tests the presentation limiter, not a complete 60 FPS gameplay patch.
#include "generated/crackdown_pch.h"
#include "native_timing.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <atomic>

REXCVAR_DEFINE_BOOL(fps60_pacing_experiment, false, "Experiments",
    "Pacing research only: increase submissions; currently accelerates gameplay.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REX_EXTERN(__imp__sub_826BC3D0);

REX_EXTERN(sub_826BC3D0) {
    // D3D's swap scheduling callback packs the vblank interval into bits 8..11.
    // Its queue services the normal SDK 60 Hz guest vblank interrupts. Leave the
    // refresh rate, unlimited interval, and other packed callback fields intact.
    if (crackdown::experiments::NativeFrameRate()) {
        // Immediate presentation; preserve fence/CPU/notification fields and
        // keep the SDK's real 60 Hz guest vblank clock running.
        ctx.r3.u32 &= ~0xF00u;
        REX_STORE_U32(0x82BAA330, 1001);
    } else if (REXCVAR_GET(fps60_pacing_experiment) &&
        ((ctx.r3.u32 >> 8) & 0xF) == 2) {
        ctx.r3.u32 = (ctx.r3.u32 & ~0xF00u) | 0x100u;
        // TU0 also caps the render worker with an integer millisecond sleep.
        // Raising only the D3D interval measured approximately 31.7 FPS because
        // this second cap remained 32. The regular build never changes it.
        REX_STORE_U32(0x82BAA330, 60);
        static std::atomic<bool> announced{false};
        if (!announced.exchange(true)) {
            REXLOG_WARN("FPS pacing experiment: guest present interval 2 -> 1; measured gameplay speed increases too");
        }
    }
    __imp__sub_826BC3D0(ctx, base);
}
