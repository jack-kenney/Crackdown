#include "experiments/timestep_policy.h"
#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <limits>

namespace {
unsigned checks;
void Check(bool value, const char* message) {
    ++checks;
    if (!value) {
        std::fprintf(stderr, "Timestep policy failed: %s\n", message);
        std::abort();
    }
}
}

int main() {
    using crackdown::experiments::PlanNativeTimestep;
    static_assert(PlanNativeTimestep(1033, 1000).step_ms == 33);
    static_assert(PlanNativeTimestep(1250, 1000).discard_ms == 200);
    for (uint32_t elapsed = 0; elapsed <= 50; ++elapsed) {
        const auto plan = PlanNativeTimestep(1000 + elapsed, 1000);
        Check(plan.step_ms == elapsed && plan.discard_ms == 0 &&
              plan.committed_before_step_ms == 1000,
              "ordinary elapsed time and zero remain unchanged");
    }
    for (uint32_t elapsed : {51u, 100u, 128u, 250u, 1000u, 2000u, 6000u,
                             std::numeric_limits<uint32_t>::max()}) {
        const auto plan = PlanNativeTimestep(1000 + elapsed, 1000);
        Check(plan.step_ms == 50 && plan.discard_ms == elapsed - 50,
              "stall debt is discarded, not retained");
        Check(uint32_t(plan.committed_before_step_ms + plan.step_ms) == uint32_t(1000 + elapsed),
              "original commit finishes exactly at sampled wallclock");
        Check(uint32_t(1000 + plan.discard_ms) == plan.committed_before_step_ms,
              "discard cursor advance is modularly consistent");
    }
    auto plan = PlanNativeTimestep(0x10, 0xfffffff0);
    Check(plan.step_ms == 32 && plan.discard_ms == 0,
          "ordinary kernel millisecond wrap is preserved");
    plan = PlanNativeTimestep(0x100, 0xfffffff0);
    Check(plan.step_ms == 50 && plan.discard_ms == 222 &&
          uint32_t(plan.committed_before_step_ms + 50) == 0x100,
          "hitch across kernel wrap discards exact debt");

    // A 250 ms stall contributes one 50 ms simulation step. Every subsequent
    // frame immediately resumes its own measured elapsed time, without bursts.
    uint32_t sampled = 1000, committed = 1000, simulated = 0, discarded = 0;
    for (const auto interval : {16u, 17u, 250u, 16u, 17u, 33u, 34u, 16u}) {
        sampled += interval;
        plan = PlanNativeTimestep(sampled, committed);
        simulated += plan.step_ms;
        discarded += plan.discard_ms;
        committed = plan.committed_before_step_ms + plan.step_ms;
        Check(plan.step_ms == (interval == 250 ? 50 : interval),
              "post-stall frames never consume historical debt");
        Check(committed == sampled && simulated + discarded == sampled - 1000,
              "discard plus simulation accounts for wallclock exactly");
    }
    Check(simulated == 199 && discarded == 200, "stall trace exact retained and discarded totals");
    plan = PlanNativeTimestep(sampled, committed);
    Check(plan.step_ms == 0 && plan.discard_ms == 0,
          "same-millisecond retry invents no delta");
    plan = PlanNativeTimestep(500, 500);
    Check(plan.step_ms == 0 && plan.committed_before_step_ms == 500,
          "freshly initialized clock has no old debt");
    std::printf("Timestep policy passed (%u checks)\n", checks);
}
