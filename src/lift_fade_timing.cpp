// TU0 cLift model fade. The original routine owns transforms and clamping;
// only the consumed reference increment uses elapsed time in the opt-in mode.
#include <rex/cvar.h>
#include <rex/logging.h>

#include <bit>
#include <cmath>
#include <fstream>
#include <mutex>

#include "crowd_timing.h"
#include "generated/crackdown_pch.h"

REXCVAR_DEFINE_BOOL(normalize_lift_fade, false, "Experiments",
                    "Elapsed-time cLift model fade; native timing only. "
                    "Experimental, restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_STRING(lift_fade_trace_path, "", "Experiments",
                      "Optional bounded cLift fade CSV trace; empty disables "
                      "tracing. Restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
constexpr uint32_t lift_vtable = 0x820C43C8, update_return = 0x825318A4;
thread_local bool normalize_call = false;
thread_local bool step_applied = false;
struct FadeScope {
    bool previous = normalize_call;
    bool previous_applied = step_applied;
    explicit FadeScope(bool enabled) {
        normalize_call = enabled;
        step_applied = false;
    }
    ~FadeScope() {
        normalize_call = previous;
        step_applied = previous_applied;
    }
};
struct Trace {
    std::mutex mutex;
    std::ofstream stream;
    std::string path;
    unsigned rows = 0;
    void Record(const std::string& requested, uint32_t object, uint32_t visual,
                double elapsed, float before, float after, bool fading_out,
                bool normalized) {
        std::lock_guard lock(mutex);
        if (path != requested) {
            stream.close();
            stream.clear();
            path = requested;
            rows = 0;
            stream.open(path, std::ios::out | std::ios::trunc);
            if (!stream) {
                REXLOG_WARN("Could not open lift fade trace {}", path);
                return;
            }
            stream
                << "object,visual,elapsed,before,after,fading_out,normalized\n";
            stream.precision(10);
        }
        if (stream && rows < 8192) {
            stream << object << ',' << visual << ',' << elapsed << ',' << before
                   << ',' << after << ',' << fading_out << ',' << normalized
                   << '\n';
            // Bound loss on a diagnostic crash without flushing every update.
            if (++rows % 64 == 0) stream.flush();
        }
    }
};
Trace trace;
bool ValidInterval(double seconds) {
    return std::isfinite(seconds) && seconds >= 0 && seconds <= double(.1f);
}
}  // namespace

void CrackdownLiftFadeStep(PPCRegister& step, PPCRegister& elapsed) {
    if (!normalize_call || !ValidInterval(elapsed.f64) ||
        step.f64 != double(1.f / 15))
        return;
    // Reference fade: (1/15) * 30 updates/sec = two units/sec, or 0.5 sec.
    step.f64 = double(float(elapsed.f64 * 2));
    step_applied = true;
}

REX_EXTERN(__imp__sub_82531228);
REX_EXTERN(sub_82531228) {
    const bool enabled =
        crackdown::NativeTimingActive() && REXCVAR_GET(normalize_lift_fade);
    const auto& path = REXCVAR_GET(lift_fade_trace_path);
    const auto object = ctx.r3.u32;
    const bool audited = base && uint32_t(ctx.lr) == update_return &&
                         (enabled || !path.empty()) && object &&
                         object <= UINT32_MAX - 2416 &&
                         REX_LOAD_U32(object) == lift_vtable;
    const auto visual =
        audited && !path.empty() ? REX_LOAD_U32(object + 2404) : 0;
    const double elapsed = ctx.f31.f64;
    const bool normalized = audited && enabled && ValidInterval(elapsed);
    const float before =
        visual ? std::bit_cast<float>(REX_LOAD_U32(visual + 136)) : 0;
    const bool fading_out = visual && REX_LOAD_U8(object + 2328);
    FadeScope scope(normalized);
    __imp__sub_82531228(ctx, base);
    if (visual && REX_LOAD_U32(object + 2404) == visual)
        trace.Record(path, object, visual, elapsed, before,
                     std::bit_cast<float>(REX_LOAD_U32(visual + 136)),
                     fading_out, step_applied);
}
