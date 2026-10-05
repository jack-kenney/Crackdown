// TU0's native elapsed-time mode. Experimental build only; default is unchanged.
#include "generated/crackdown_pch.h"
#include "native_timing.h"
#include "frame_pacing.h"
#include "timestep_policy.h"
#include "src/crowd_timing.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <atomic>
#include <bit>
#include <thread>

REXCVAR_DEFINE_INT32(native_frame_rate, 0, "Experiments",
    "Experimental native timestep target: 0 (original), 60, 120, 144 or 240. Restart required.")
    .validator([](std::string_view value) {
        return value == "0" || value == "60" || value == "120" || value == "144" || value == "240";
    }).lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REXCVAR_DEFINE_BOOL(native_discard_hitch_time, true, "Experiments",
    "Discard excess wall time after stalls instead of accelerating later frames; native timing only.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
thread_local bool native_clock_active = false;
struct NativeClockScope {
    bool previous = native_clock_active;
    NativeClockScope() { native_clock_active = true; }
    ~NativeClockScope() { native_clock_active = previous; }
};
}

namespace crackdown::experiments {
unsigned NativeFrameRate() {
    const auto target = REXCVAR_GET(native_frame_rate);
    return target == 60 || target == 120 || target == 144 || target == 240 ? target : 0;
}
void PrepareNativeUpdate(PPCContext& ctx, uint8_t* base) {
    if (!NativeFrameRate() || uint32_t(ctx.lr) != 0x826A7774) return;
    // The caller overwrites the timer's output with 1/30. Match its original
    // scale, but use the elapsed milliseconds committed by the native clock.
    const float scale = std::bit_cast<float>(REX_LOAD_U32(0x82CFE4B4));
    ctx.f1.f64 = double(float(REX_LOAD_U32(0x82D99128) * 0.001f * scale));
}
}

REX_EXTERN(__imp__sub_823263D8);
REX_EXTERN(sub_823263D8) {
    using namespace crackdown::experiments;
    const auto target = NativeFrameRate();
    crackdown::SetCrowdNativeTiming(target != 0);
    // Only the audited main-loop call; replay or other callers keep their ABI.
    if (!target || uint32_t(ctx.lr) != 0x826A774C) {
        __imp__sub_823263D8(ctx, base);
        return;
    }
    thread_local FramePacer pacer;
    std::this_thread::sleep_until(pacer.Deadline(FrameClock::now(), FramePacer::PeriodForFps(target)));
    const auto engine = ctx.r3.u32;
    REX_STORE_U8(engine + 13, 0);
    REX_STORE_U32(engine + 40, 1);
    REX_STORE_U32(engine + 44, 100);
    // A positive divisor with an integer 1000/cap budget of zero. Zero traps.
    REX_STORE_U32(0x82BAA330, 1001);
    const PPCContext incoming = ctx;
    const auto run_output = ctx.r5.u32;
    NativeClockScope clock_scope;
    __imp__sub_823263D8(ctx, base);
    // Startup/reset can land on the same cached kernel millisecond. Retry the
    // actual timer, preserving its residual accounting; never invent a delta.
    for (unsigned retry = 0; !REX_LOAD_U8(run_output) && retry < 3; ++retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        ctx = incoming;
        __imp__sub_823263D8(ctx, base);
    }
    if (!REX_LOAD_U8(run_output)) {
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) REXLOG_WARN("Native timestep clock remained at zero after three retries; skipping core simulation");
    }
    static std::atomic<bool> announced{false};
    if (!announced.exchange(true)) {
        REXLOG_WARN("Native timestep experiment: target {} FPS, elapsed-time gameplay and physics; validation incomplete", target);
    }
}

REX_EXTERN(__imp__sub_823260C8);
REX_EXTERN(sub_823260C8) {
    const auto engine = ctx.r3.u32;
    const auto caller = uint32_t(ctx.lr);
    __imp__sub_823260C8(ctx, base);
    if (!native_clock_active || caller != 0x82326430 ||
        !REXCVAR_GET(native_discard_hitch_time) || REX_LOAD_U32(0x82DE3F90)) return;
    const auto plan = crackdown::experiments::PlanNativeTimestep(
        REX_LOAD_U32(engine + 20), REX_LOAD_U32(engine + 36));
    if (!plan.discard_ms) return;
    // Match the original 82326248 discard ledger. Keep published simulation
    // milliseconds and floating accumulated simulation time untouched; the
    // original commit publishes only the retained step. Moving the committed
    // wall cursor is essential: a smaller max alone retains catch-up debt.
    REX_STORE_U32(engine + 32, REX_LOAD_U32(engine + 32) + plan.discard_ms);
    REX_STORE_U32(engine + 64, REX_LOAD_U32(engine + 64) + plan.discard_ms);
    REX_STORE_U32(engine + 36, plan.committed_before_step_ms);
}

REX_EXTERN(__imp__sub_82325850);
REX_EXTERN(sub_82325850) {
    const bool native = crackdown::experiments::NativeFrameRate() != 0;
    __imp__sub_82325850(ctx, base);
    if (native && REX_LOAD_U8(0x82DE3FB0) && !REX_LOAD_U32(0x82D99128)) {
        // If the cached clock did not advance even after bounded retries,
        // retain housekeeping but skip the main simulation dispatch.
        const auto engine = REX_LOAD_U32(0x82DE25C0);
        if (engine) REX_STORE_U8(engine + 14, 1);
    }
}

REX_EXTERN(__imp__sub_82324938);
REX_EXTERN(sub_82324938) {
    if (crackdown::experiments::NativeFrameRate() && uint32_t(ctx.lr) == 0x826A7940) {
        const auto engine = REX_LOAD_U32(0x82DE25C0);
        if (engine && !REX_LOAD_U8(engine + 13)) {
            // Native mode otherwise freezes this frame-indexed deadline clock.
            // Keep a logical 30 Hz cadence, preserving exact-equality deadlines:
            // never skip an index, even when a hitch leaves a backlog.
            thread_local uint32_t last_engine = 0, last_time = 0, last_counter = 0, remainder = 0;
            const auto elapsed = REX_LOAD_U32(engine + 32);
            auto counter = REX_LOAD_U32(engine + 16);
            // Session transitions can reset the logical counter independently
            // of the elapsed clock. Do not retain the previous session's phase
            // or backlog. Record our post-increment value so an ordinary wrap
            // produced here is not mistaken for an external reset next time.
            if (last_engine != engine || elapsed < last_time || counter < last_counter)
                remainder = 0;
            last_engine = engine; last_time = elapsed;
            if (!REX_LOAD_U8(engine + 14)) {
                remainder += REX_LOAD_U32(0x82D99128) * 30;
                if (remainder >= 1000) {
                    remainder -= 1000;
                    REX_STORE_U32(engine + 16, ++counter);
                }
            }
            last_counter = counter;
        }
    }
    __imp__sub_82324938(ctx, base);
}
