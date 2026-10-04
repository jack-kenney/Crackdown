#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

#include <rex/cvar.h>
#include "crackdown_pch.h"

REXCVAR_DECLARE(bool, fix_audio_barrier);
extern "C" REX_FUNC(sub_82A50000);
static unsigned fallback_calls = 0;
REX_EXTERN(__imp__sub_82A50000) { ++fallback_calls; }

static void Check(bool value, const char* message) {
    if (!value) { std::fprintf(stderr, "%s\n", message); std::exit(1); }
}

static void Exercise(uint8_t mask, unsigned rounds, bool alternating) {
    // Engine+356 is eight-byte aligned, as in the observed TU0 allocations.
    std::vector<uint8_t> memory(65536 + 31, 0);
    uint8_t* base = reinterpret_cast<uint8_t*>((reinterpret_cast<uintptr_t>(memory.data()) + 31) & ~uintptr_t(31));
    constexpr uint32_t engine = 0x204;
    unsigned count = 0;
    for (unsigned cpu = 0; cpu < 6; ++cpu) {
        if (mask & (1u << cpu)) {
            ++count;
            REX_STORE_U32(engine + 308 + cpu * 4, 0x100 + cpu);
        }
        REX_STORE_U8(0x2000 + cpu * 0x300 + 268, cpu);
    }
    REX_STORE_U32(engine + 304, count);
    std::array<std::atomic<unsigned>, 6> entered{};
    std::atomic<unsigned> finished{0};
    std::vector<std::thread> workers;
    for (unsigned cpu = 0; cpu < 6; ++cpu) {
        if (!(mask & (1u << cpu))) continue;
        workers.emplace_back([&, cpu] {
            PPCContext ctx{};
            ctx.r1.u32 = 0x1000;
            ctx.r3.u32 = engine;
            ctx.r13.u32 = 0x2000 + cpu * 0x300;
            for (unsigned round = 1; round <= rounds; ++round) {
                // Five rendezvous per frame (A,B,A,B,A) also exercises A,A
                // reuse at frame boundaries. Uneven workers expose early exits.
                ctx.r4.u32 = engine + 356 + (alternating && (round - 1) % 5 % 2 == 1 ? 8 : 0);
                if ((round + cpu) % 17 == 0) std::this_thread::yield();
                if (cpu == 5 && round % 701 == 0)
                    std::this_thread::sleep_for(std::chrono::microseconds(50));
                entered[cpu].store(round, std::memory_order_release);
                sub_82A50000(ctx, base);
                for (unsigned other = 0; other < 6; ++other) {
                    if (mask & (1u << other))
                        Check(entered[other].load(std::memory_order_acquire) >= round,
                              "Audio worker escaped before every participant arrived");
                }
                Check(ctx.r1.u32 == 0x1000 && ctx.r3.u32 == engine &&
                      ctx.r13.u32 == 0x2000 + cpu * 0x300,
                      "Audio barrier changed preserved guest state");
            }
            ++finished;
        });
    }
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
    while (finished.load() != count) {
        Check(std::chrono::steady_clock::now() < deadline, "Audio barrier stalled");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    for (auto& worker : workers) worker.join();
    Check(REX_LOAD_U64(engine + 356) == 0 && REX_LOAD_U64(engine + 364) == 0,
          "Audio barrier left guest completion flags set");
}

int main() {
    std::vector<uint8_t> memory(65536 + 31, 0);
    uint8_t* base = reinterpret_cast<uint8_t*>((reinterpret_cast<uintptr_t>(memory.data()) + 31) & ~uintptr_t(31));
    PPCContext ctx{};
    ctx.r3.u32 = 0x204;
    ctx.r4.u32 = 0x368;
    ctx.r13.u32 = 0x2000;
    sub_82A50000(ctx, base); // No workers: return immediately.
    Check(fallback_calls == 0, "Empty worker pool should bypass the barrier");
    REXCVAR_SET(fix_audio_barrier, false);
    sub_82A50000(ctx, base);
    Check(fallback_calls == 1, "Disabled fix did not dispatch to guest code");
    REXCVAR_SET(fix_audio_barrier, true);
    Exercise(0x30, 15000, true);
    Exercise(0x30, 10000, false);
    Exercise(0x3F, 10000, true);
    Exercise(0x01, 1000, false);
    std::puts("Audio barrier generations, uneven workers, CPU masks and fallback passed");
}
