#include "crackdown_pch.h"

#include <cmath>
#include <rex/cvar.h>

// TU0 uses a reference camera angle rather than a conventional vertical FOV.
// Scale the camera snapshot consumed by both projection and culling; never
// change the camera object's evolving base angle or its aiming/zoom state.
REXCVAR_DEFINE_DOUBLE(camera_fov_scale, 1.0, "Game Enhancements",
                      "Experimental normal-camera field-of-view multiplier (0.75-1.5). "
                      "1 keeps the original camera; other camera modes are unchanged.")
    .range(0.75, 1.5)
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REX_EXTERN(__imp__sub_82284FA0);

REX_EXTERN(sub_82284FA0) {
    const uint32_t holder = ctx.r3.u32;
    __imp__sub_82284FA0(ctx, base);
    const double scale = REXCVAR_GET(camera_fov_scale);
    if (scale == 1.0 || !std::isfinite(scale) || scale < 0.75 || scale > 1.5) return;

    const uint32_t manager = REX_LOAD_U32(0x82DE2574);
    if (!manager || holder != manager + 272) return;  // Main player viewport only.
    const uint32_t camera = REX_LOAD_U32(holder);
    if (!camera || REX_LOAD_U32(camera + 6500) != 3 ||
        REX_LOAD_U32(camera + 6508) != 0) return;  // Active default camera mode.

    // The actual guest routine leaves r11 pointing to the snapshot it wrote.
    // Use that address instead of re-reading the global triple-buffer index,
    // which the consumer may advance on another thread after the guest returns.
    const uint32_t snapshot = ctx.r11.u32;
    if (snapshot < holder || snapshot - holder > 192 ||
        (snapshot - holder) % 96 != 0) return;
    PPCRegister angle{};
    angle.u32 = REX_LOAD_U32(snapshot + 80);
    const float adjusted = float(double(angle.f32) * scale);
    if (!std::isfinite(angle.f32) || angle.f32 <= 0 ||
        !std::isfinite(adjusted) || adjusted <= 0 || adjusted >= 150) return;
    angle.f32 = adjusted;
    REX_STORE_U32(snapshot + 80, angle.u32);
}
