#pragma once
#include <cmath>
#include <cstdint>
struct PPCContext;

namespace crackdown::experiments {
// TU0 sub_822B4D40 returns -0.4 * height / dt. Preserve the original
// 30 Hz correction response, instead of repeating 40% at every native update.
inline double StepDownCorrectionScale(double dt) {
    constexpr double reference_dt = 1.0 / 30.0;
    if (!std::isfinite(dt) || dt <= 0 || dt >= reference_dt) return 1;
    return -std::expm1(std::log(0.6) * dt / reference_dt) / 0.4;
}
void StepDownWithCorrection(PPCContext& ctx, uint8_t* base);
}
