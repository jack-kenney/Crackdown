// Narrow elapsed-time correction for the audited TU0 local character controller.
#include "generated/crackdown_pch.h"
#include "character_step_down.h"
#include "native_timing.h"
#include <rex/cvar.h>
#include <bit>

REXCVAR_DEFINE_BOOL(normalize_character_step_down, true, "Experiments",
    "Elapsed-time character step-down correction; native timing only. Restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REX_EXTERN(__imp__sub_822B4D40);
namespace crackdown::experiments {
void StepDownWithCorrection(PPCContext& ctx, uint8_t* base) {
    // Save inputs before original dispatch can overwrite argument registers.
    const bool eligible = REXCVAR_GET(normalize_character_step_down) && NativeFrameRate() &&
        uint32_t(ctx.lr) == 0x822AA884 && ctx.r5.u32 != 0;
    const auto output = ctx.r5.u32;
    const double scale = eligible ? StepDownCorrectionScale(ctx.f1.f64) : 1;
    __imp__sub_822B4D40(ctx, base);
    if (scale == 1 || (ctx.r3.u32 & 0xff) == 0) return;
    const float vertical = std::bit_cast<float>(REX_LOAD_U32(output + 4));
    if (!std::isfinite(vertical) || vertical >= 0) return;
    REX_STORE_U32(output + 4, std::bit_cast<uint32_t>(float(vertical * scale)));
}
}
