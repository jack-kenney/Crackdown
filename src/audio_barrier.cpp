#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <unordered_map>

#include <rex/cvar.h>
#include "crackdown_pch.h"

REXCVAR_DEFINE_BOOL(fix_audio_barrier, true, "Audio",
    "Synchronize Crackdown TU0 mixing workers by barrier generation (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

extern "C" REX_FUNC(__imp__sub_82A50000);

namespace {
struct BarrierState {
    std::mutex mutex;
    std::condition_variable changed;
    uint64_t generation = 0;
    uint8_t arrivals = 0;
};

// Key by host address so separate guest memory mappings cannot share a barrier.
std::mutex states_mutex;
std::unordered_map<uint8_t*, std::unique_ptr<BarrierState>> states;
}

// TU0's mixing workers rendezvous using six bytes followed by an eight-byte
// reset. Every worker can reset the completed round; at the boundary between
// frames that reset can erase an arrival for the next round. Keep arrivals and
// completion under one lock and release waiters by generation instead.
REX_EXTERN(sub_82A50000) {
    if (!REXCVAR_GET(fix_audio_barrier)) {
        __imp__sub_82A50000(ctx, base);
        return;
    }
    const uint32_t engine = ctx.r3.u32;
    const uint32_t barrier = ctx.r4.u32;
    if (!REX_LOAD_U32(engine + 304)) return;

    uint8_t expected = 0;
    for (unsigned cpu = 0; cpu < 6; ++cpu) {
        if (REX_LOAD_U32(engine + 308 + cpu * 4)) expected |= uint8_t(1u << cpu);
    }
    const unsigned cpu = REX_LOAD_U8(ctx.r13.u32 + 268);
    // Configured workers must have a valid Xenon CPU and a corresponding handle.
    if (cpu >= 6 || !(expected & (1u << cpu))) {
        __imp__sub_82A50000(ctx, base);
        return;
    }

    BarrierState* state;
    {
        std::lock_guard lock(states_mutex);
        auto& entry = states[REX_RAW_ADDR(barrier)];
        if (!entry) entry = std::make_unique<BarrierState>();
        state = entry.get();
    }
    std::unique_lock lock(state->mutex);
    const uint64_t generation = state->generation;
    state->arrivals |= uint8_t(1u << cpu);
    if (state->arrivals == expected) {
        REX_STORE_U64(barrier, 0);
        state->arrivals = 0;
        ++state->generation;
        lock.unlock();
        state->changed.notify_all();
    } else {
        state->changed.wait(lock, [&] { return state->generation != generation; });
    }
}
