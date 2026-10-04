#pragma once
#include <cstdint>

namespace crackdown::experiments {

// Preserve ordinary 30/60 Hz elapsed time and modest frame-time jitter. After
// a stall, simulate at most one bounded step and discard the older elapsed
// time. Lowering only the guest timer's max limit would retain catch-up debt.
inline constexpr uint32_t kMaximumNativeStepMs = 50;

struct NativeTimestepPlan {
    uint32_t step_ms;
    uint32_t discard_ms;
    uint32_t committed_before_step_ms;
};

// The guest kernel clock and committed cursor are wrapping uint32 milliseconds.
// The caller applies discard_ms through the guest's existing discard ledger
// (+32/+64), then writes committed_before_step_ms to engine+36. The original
// timer commits step_ms, publishing all delta/cumulative simulation outputs.
// A zero step remains zero: minimum-step checks and retries retain their ABI.
constexpr NativeTimestepPlan PlanNativeTimestep(uint32_t sampled_ms,
                                                uint32_t committed_ms) {
    const uint32_t elapsed_ms = sampled_ms - committed_ms;
    const uint32_t step_ms = elapsed_ms > kMaximumNativeStepMs
                                ? kMaximumNativeStepMs : elapsed_ms;
    return {step_ms, elapsed_ms - step_ms, sampled_ms - step_ms};
}

} // namespace crackdown::experiments
