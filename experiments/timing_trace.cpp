// Opt-in, aggregated observations of the original guest timing routines.
#include "generated/crackdown_pch.h"
#include "native_timing.h"
#include <rex/cvar.h>
#include <chrono>
#include <fstream>
#include <map>
#include <mutex>
#include <thread>
#include <tuple>
#include <limits>

REXCVAR_DEFINE_STRING(timing_trace_path, "", "Experiments",
    "Write one-second aggregates of guest timing, waits and presentation intervals to CSV.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
using Clock = std::chrono::steady_clock;
struct Aggregate {
    unsigned count = 0;
    double sum = 0, minimum = std::numeric_limits<double>::infinity();
    double maximum = -std::numeric_limits<double>::infinity();
};
struct Trace {
    std::mutex mutex;
    std::ofstream output;
    Clock::time_point start = Clock::now(), last = start;
    using Key = std::tuple<const char*, uint32_t, std::thread::id>;
    std::map<Key, Aggregate> rows;
    Trace() : output(REXCVAR_GET(timing_trace_path)) {
        output << "seconds,kind,caller,thread,calls,sum,minimum,maximum,fixed,variable,delta_ms,min_ms,max_ms,engine_frames,residual_ms,discarded_ms\n";
    }
};
void Record(const char* kind, uint32_t caller, double dt, uint8_t* base, bool flush = false) {
    if (REXCVAR_GET(timing_trace_path).empty()) return;
    static Trace trace;
    std::lock_guard guard(trace.mutex);
    auto& row = trace.rows[{kind, caller, std::this_thread::get_id()}];
    ++row.count; row.sum += dt;
    if (dt < row.minimum) row.minimum = dt;
    if (dt > row.maximum) row.maximum = dt;
    if (!flush) return;
    auto now = Clock::now();
    if (now - trace.last < std::chrono::seconds(1)) return;
    const auto engine = REX_LOAD_U32(0x82DE25C0);
    for (const auto& [key, value] : trace.rows) {
        const auto& [name, lr, thread] = key;
        trace.output << std::chrono::duration<double>(now-trace.start).count() << ','
            << name << ',' << std::hex << lr << std::dec << ',' << thread << ','
            << value.count << ',' << value.sum << ',' << value.minimum << ',' << value.maximum << ','
            << (engine ? unsigned(REX_LOAD_U8(engine+13)) : 0) << ','
            << unsigned(REX_LOAD_U8(0x82DE3FB0)) << ',' << REX_LOAD_U32(0x82D99128) << ','
            << (engine ? REX_LOAD_U32(engine+40) : 0) << ','
            << (engine ? REX_LOAD_U32(engine+44) : 0) << ','
            << (engine ? REX_LOAD_U32(engine+16) : 0) << ','
            << (engine ? uint32_t(REX_LOAD_U32(engine+20) - REX_LOAD_U32(engine+36)) : 0) << ','
            << (engine ? REX_LOAD_U32(engine+64) : 0) << '\n';
    }
    trace.output.flush(); trace.rows.clear(); trace.last = now;
}
}

REX_EXTERN(__imp__sub_8254C568);
REX_EXTERN(sub_8254C568) {
    const uint32_t caller = uint32_t(ctx.lr);
    __imp__sub_8254C568(ctx, base);
    Record("delta", caller, ctx.f1.f64, base);
}

REX_EXTERN(__imp__sub_826A7698);
REX_EXTERN(sub_826A7698) {
    Record("frame", uint32_t(ctx.lr), 0, base, true);
    __imp__sub_826A7698(ctx, base);
}

REX_EXTERN(__imp__sub_826A6B50);
REX_EXTERN(sub_826A6B50) {
    crackdown::experiments::PrepareNativeUpdate(ctx, base);
    Record("update", uint32_t(ctx.lr), ctx.f1.f64, base);
    __imp__sub_826A6B50(ctx, base);
}

REX_EXTERN(__imp__sub_82326348);
REX_EXTERN(sub_82326348) {
    Record("clock_commit", uint32_t(ctx.lr), ctx.r4.u32 * 0.001, base);
    __imp__sub_82326348(ctx, base);
}

REX_EXTERN(__imp__sub_82743788);
REX_EXTERN(sub_82743788) {
    if (REXCVAR_GET(timing_trace_path).empty()) {
        __imp__sub_82743788(ctx, base);
        return;
    }
    const uint32_t caller = uint32_t(ctx.lr);
    const double requested = ctx.r3.u32 * 0.001;
    const auto start = Clock::now();
    __imp__sub_82743788(ctx, base);
    const double elapsed = std::chrono::duration<double>(Clock::now() - start).count();
    Record("sleep_request", caller, requested, base);
    Record("sleep", caller, elapsed, base);
}

REX_EXTERN(__imp__sub_82743390);
REX_EXTERN(sub_82743390) {
    if (REXCVAR_GET(timing_trace_path).empty()) {
        __imp__sub_82743390(ctx, base);
        return;
    }
    const uint32_t caller = uint32_t(ctx.lr);
    const auto start = Clock::now();
    __imp__sub_82743390(ctx, base);
    Record("wait", caller,
           std::chrono::duration<double>(Clock::now() - start).count(), base);
}

REX_EXTERN(__imp__sub_826BC568);
REX_EXTERN(sub_826BC568) {
    if (!REXCVAR_GET(timing_trace_path).empty()) {
        // Record the raw D3D enum before the guest encodes the callback packet:
        // 0/1 = one vblank, 2 = two, 4 = three, 0x80000000 = immediate.
        // This row's values are enum values; other duration rows use seconds.
        Record("present_interval", uint32_t(ctx.lr),
               double(REX_LOAD_U32(ctx.r3.u32 + 13220)), base);
    }
    __imp__sub_826BC568(ctx, base);
}
