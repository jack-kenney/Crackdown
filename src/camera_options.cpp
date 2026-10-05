#include "crackdown_pch.h"

#include <cmath>
#include <rex/cvar.h>

// TU0 uses a reference camera angle rather than a conventional vertical FOV.
// Keep the render snapshot and the simulation's projection/frustum caches at
// the same angle, without changing the evolving base angle or aiming state.
REXCVAR_DEFINE_DOUBLE(camera_fov_scale, 1.0, "Game Enhancements",
                      "Experimental normal-camera field-of-view multiplier (0.75-1.5). "
                      "1 keeps the original camera; other camera modes are unchanged.")
    .range(0.75, 1.5)
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REX_EXTERN(__imp__sub_82284FA0);
REX_EXTERN(__imp__sub_82AB9998);

namespace {
bool AdjustCameraAngle(uint8_t* base, uint32_t camera, float& angle) {
    const double scale = REXCVAR_GET(camera_fov_scale);
    if (scale == 1.0 || !std::isfinite(scale) || scale < 0.75 || scale > 1.5) return false;
    const uint32_t manager = REX_LOAD_U32(0x82DE2574);
    if (!manager || !camera || REX_LOAD_U32(manager + 272) != camera ||
        REX_LOAD_U32(camera + 6500) != 3 || REX_LOAD_U32(camera + 6508) != 0) return false;
    const float adjusted = float(double(angle) * scale);
    if (!std::isfinite(angle) || angle <= 0 ||
        !std::isfinite(adjusted) || adjusted <= 0 || adjusted >= 150) return false;
    angle = adjusted;
    return true;
}
}

REX_EXTERN(sub_82AB9998) {
    // These two camera routines compute tan(reference_angle / 2). The first
    // builds the screen projection used by overhead icons (sub_822969E8); the
    // second builds the simulation frustum. Other tangent calls are unchanged.
    if (ctx.lr == 0x822967B8 || ctx.lr == 0x82295D6C) {
        float angle = float(ctx.f13.f64 * 2.0);
        if (AdjustCameraAngle(base, ctx.r31.u32, angle)) {
            // Match the guest's two rounded fmuls operations, using its degree
            // conversion constant already in f0. Only the tangent input changes.
            const float halfAngle = float(double(angle) * 0.5);
            ctx.f1.f64 = double(float(double(halfAngle) * ctx.f0.f64));
        }
    }
    __imp__sub_82AB9998(ctx, base);
}

REX_EXTERN(sub_82284FA0) {
    const uint32_t holder = ctx.r3.u32;
    __imp__sub_82284FA0(ctx, base);
    const uint32_t manager = REX_LOAD_U32(0x82DE2574);
    if (!manager || holder != manager + 272) return;  // Main player viewport only.
    const uint32_t camera = REX_LOAD_U32(holder);

    // The actual guest routine leaves r11 pointing to the snapshot it wrote.
    // Use that address instead of re-reading the global triple-buffer index,
    // which the consumer may advance on another thread after the guest returns.
    const uint32_t snapshot = ctx.r11.u32;
    if (snapshot < holder || snapshot - holder > 192 ||
        (snapshot - holder) % 96 != 0) return;
    PPCRegister angle{};
    angle.u32 = REX_LOAD_U32(snapshot + 80);
    if (!AdjustCameraAngle(base, camera, angle.f32)) return;
    REX_STORE_U32(snapshot + 80, angle.u32);
}
