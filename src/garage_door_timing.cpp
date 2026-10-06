// TU0 cAgencyGarageDoor timeout. Retain the original state machine, proximity
// tests and timer resets; replace only its consumed reference increment.
#include <rex/cvar.h>
#include <rex/logging.h>

#include <bit>
#include <cmath>
#include <fstream>
#include <mutex>

#include "crowd_timing.h"
#include "generated/crackdown_pch.h"

REXCVAR_DEFINE_BOOL(normalize_garage_door_timer, false, "Experiments",
                    "Elapsed-time Agency garage door timeout; native timing "
                    "only. Experimental, restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_STRING(garage_door_trace_path, "", "Experiments",
                      "Optional bounded garage door timer CSV trace; empty "
                      "disables tracing. Restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
constexpr uint32_t door_vtable = 0x820C36F0;
thread_local bool normalize_call = false;
thread_local bool step_applied = false;
thread_local double elapsed_seconds = 0;

struct TimerScope {
    bool previous = normalize_call;
    bool previous_applied = step_applied;
    double previous_elapsed = elapsed_seconds;
    TimerScope(bool enabled, double seconds) {
        normalize_call = enabled;
        step_applied = false;
        elapsed_seconds = seconds;
    }
    ~TimerScope() {
        normalize_call = previous;
        step_applied = previous_applied;
        elapsed_seconds = previous_elapsed;
    }
};

bool ValidInterval(double seconds) {
    return std::isfinite(seconds) && seconds >= 0 && seconds <= double(.1f);
}

struct Trace {
    std::mutex mutex;
    std::ofstream stream;
    std::string path;
    unsigned rows = 0;
    void Record(const std::string& requested, uint32_t object, double elapsed,
                float before, float after, uint32_t state_before,
                uint32_t state_after, bool normalized) {
        std::lock_guard lock(mutex);
        if (path != requested) {
            stream.close();
            stream.clear();
            path = requested;
            rows = 0;
            stream.open(path, std::ios::out | std::ios::trunc);
            if (!stream) {
                REXLOG_WARN("Could not open garage door trace {}", path);
                return;
            }
            stream << "object,elapsed,before,after,state_before,state_after,"
                      "normalized\n";
            stream.precision(10);
        }
        if (stream && rows < 8192) {
            stream << object << ',' << elapsed << ',' << before << ',' << after
                   << ',' << state_before << ',' << state_after << ','
                   << normalized << '\n';
            if (++rows % 64 == 0) stream.flush();
        }
    }
};
Trace trace;
}  // namespace

void CrackdownGarageDoorTimerStep(PPCRegister& step) {
    if (!normalize_call || !ValidInterval(elapsed_seconds) ||
        step.f64 != double(1.f / 30))
        return;
    // Reference timer: (1/30) * 30 updates/sec = one elapsed second/sec.
    step.f64 = double(float(elapsed_seconds));
    step_applied = true;
}

REX_EXTERN(__imp__sub_8252EDA0);
REX_EXTERN(sub_8252EDA0) {
    const bool enabled = crackdown::NativeTimingActive() &&
                         REXCVAR_GET(normalize_garage_door_timer);
    const auto& path = REXCVAR_GET(garage_door_trace_path);
    const auto object = ctx.r3.u32;
    const bool audited = base && (enabled || !path.empty()) && object &&
                         object <= UINT32_MAX - 2408 &&
                         REX_LOAD_U32(object) == door_vtable;
    // The base object update legally clobbers volatile f1 before the timer.
    // Capture the update argument here, rather than reading f1 at the hook.
    const double elapsed = ctx.f1.f64;
    const bool tracing = audited && !path.empty();
    const float before =
        tracing ? std::bit_cast<float>(REX_LOAD_U32(object + 2404)) : 0;
    const uint32_t state_before = tracing ? REX_LOAD_U32(object + 2368) : 0;
    TimerScope scope(audited && enabled && ValidInterval(elapsed), elapsed);
    __imp__sub_8252EDA0(ctx, base);
    if (tracing)
        trace.Record(path, object, elapsed, before,
                     std::bit_cast<float>(REX_LOAD_U32(object + 2404)),
                     state_before, REX_LOAD_U32(object + 2368), step_applied);
}
