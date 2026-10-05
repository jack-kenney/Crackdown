#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace crackdown {
struct CrowdStepPlan {
    double displacement_scale = 1, fraction = 0;
    uint32_t remaining = 0;
};

// Segment counts are authored in reference updates, not native updates.
// Retain the fractional reference update instead of exhausting a path faster
// merely because the renderer and simulation now run more often.
inline CrowdStepPlan PlanCrowdStep(uint32_t remaining, double fraction, double frames) {
    if (!remaining || remaining > INT32_MAX || !std::isfinite(fraction) ||
        fraction < 0 || fraction >= 1 || !std::isfinite(frames) || frames <= 0)
        return {1, 0, remaining};
    const double scale = std::min(frames, double(remaining) - fraction);
    const double consumed = fraction + scale;
    const auto whole = std::min(remaining, uint32_t(std::floor(consumed + 1e-10)));
    return {scale, remaining == whole ? 0 : std::max(0.0, consumed - whole), remaining - whole};
}

inline double CrowdSteeringGain(double original, double frames) {
    if (!std::isfinite(original) || original <= 0 || original >= 1 ||
        !std::isfinite(frames) || frames <= 0 || frames == 1) return original;
    return -std::expm1(std::log1p(-original) * frames);
}
}
