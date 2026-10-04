// TU0's native elapsed-time mode. Experimental build only; default is unchanged.
#include "generated/crackdown_pch.h"
#include "native_timing.h"
#include "frame_pacing.h"
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
            thread_local uint32_t last_engine = 0, last_time = 0, remainder = 0;
            const auto elapsed = REX_LOAD_U32(engine + 32);
            if (last_engine != engine || elapsed < last_time) remainder = 0;
            last_engine = engine; last_time = elapsed;
            if (!REX_LOAD_U8(engine + 14)) {
                remainder += REX_LOAD_U32(0x82D99128) * 30;
                if (remainder >= 1000) {
                    remainder -= 1000;
                    REX_STORE_U32(engine + 16, REX_LOAD_U32(engine + 16) + 1);
                }
            }
        }
    }
    __imp__sub_82324938(ctx, base);
}
