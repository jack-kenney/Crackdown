#include "generated/crackdown_pch.h"
#include "native_timing.h"
#include <rex/cvar.h>
#include <windows.h>
#include <array>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <initializer_list>

REX_EXTERN(sub_823263D8);
REX_EXTERN(sub_82325850);
REX_EXTERN(sub_82324938);
REX_EXTERN(sub_823260C8);

namespace {
constexpr uint32_t engine_address = 0x100000;
constexpr uint32_t run_address = 0x110001;
constexpr uint32_t clamp_address = 0x110000;
constexpr uint32_t delta_address = 0x110008;
unsigned checks, timer_calls, pause_calls, counter_calls, clock_calls;
bool expect_native_timer, mutate_timer_context;
bool timer_samples_clock;
uint32_t clock_sample_ms, clock_caller = 0x82326430;
uint8_t original_pause;
PPCContext incoming;
std::array<uint32_t, 4> timer_deltas{};
unsigned delta_count, delta_index;

void Check(bool condition, const char* message) {
    ++checks;
    if (!condition) {
        std::fprintf(stderr, "Native timing test failed: %s\n", message);
        std::abort();
    }
}
void Select(unsigned target) {
    Check(rex::cvar::SetFlagByName("native_frame_rate", std::to_string(target)), "set target");
    Check(crackdown::experiments::NativeFrameRate() == target, "read target");
}
void Queue(std::initializer_list<uint32_t> deltas) {
    delta_count = unsigned(deltas.size());
    delta_index = 0;
    unsigned index = 0;
    for (const auto delta : deltas) timer_deltas[index++] = delta;
}
PPCContext TimerContext() {
    PPCContext ctx{};
    ctx.r3.u64 = engine_address;
    ctx.r4.u64 = clamp_address;
    ctx.r5.u64 = run_address;
    ctx.r6.u64 = delta_address;
    ctx.r27.u64 = 0x123456789abcdef0ull;
    ctx.lr = 0x826A774C;
    return ctx;
}
void ResetEngine(uint8_t* base) {
    REX_STORE_U32(0x82DE25C0, engine_address);
    REX_STORE_U8(engine_address + 13, 1);
    REX_STORE_U8(engine_address + 14, 0);
    REX_STORE_U32(engine_address + 16, 0);
    REX_STORE_U32(engine_address + 32, 0);
    REX_STORE_U32(engine_address + 36, 500);
    REX_STORE_U32(engine_address + 40, 33);
    REX_STORE_U32(engine_address + 44, 33);
    REX_STORE_U32(engine_address + 64, 0);
    REX_STORE_U32(engine_address + 60, std::bit_cast<uint32_t>(0.0f));
    REX_STORE_U32(0x82BAA330, 32);
    REX_STORE_U8(0x82DE3FB0, 0);
    REX_STORE_U32(0x82D99128, 0);
    REX_STORE_U32(0x82D9912C, 0);
    REX_STORE_U32(0x82DE3F90, 0);
}
}

// These original boundaries model timer output and a real commit. The earlier
// tests/native_timing_test.py separately exercises the generated timer itself.
REX_EXTERN(__imp__sub_823315E0) { Check(false, "clock hook cannot allocate crowd objects"); }
REX_EXTERN(__imp__sub_82330038) { Check(false, "clock hook cannot update crowd objects"); }
REX_EXTERN(__imp__sub_82338220) { Check(false, "clock hook cannot update crowd steering"); }
REX_EXTERN(__imp__sub_82333538) { Check(false, "clock hook cannot update background cars"); }
REX_EXTERN(__imp__sub_823263D8) {
    ++timer_calls;
    Check(ctx.r3.u64 == incoming.r3.u64 && ctx.r4.u64 == incoming.r4.u64 &&
          ctx.r5.u64 == incoming.r5.u64 && ctx.r6.u64 == incoming.r6.u64 &&
          ctx.r27.u64 == incoming.r27.u64 && ctx.lr == incoming.lr,
          "original receives saved incoming registers on every retry");
    if (expect_native_timer) {
        Check(REX_LOAD_U8(engine_address + 13) == 0, "native mode before original");
        Check(REX_LOAD_U32(engine_address + 40) == 1 &&
              REX_LOAD_U32(engine_address + 44) == 100, "native min/max limits");
        Check(REX_LOAD_U32(0x82BAA330) == 1001, "positive worker divisor");
    }
    Check(delta_index < delta_count, "bounded expected timer calls");
    auto dt = timer_deltas[delta_index++];
    if (timer_samples_clock) {
        const auto previous_delta = REX_LOAD_U32(0x82D99128);
        const auto previous_sum = REX_LOAD_U32(0x82D9912C);
        const auto previous_float_sum = REX_LOAD_U32(engine_address + 60);
        auto clock_ctx = ctx;
        clock_ctx.r3.u64 = engine_address;
        clock_ctx.lr = clock_caller;
        sub_823260C8(clock_ctx, base);
        Check(clock_ctx.r3.u64 == 0xaabbccddeeff0011ull &&
              clock_ctx.r5.u64 == 0x1122334455667788ull &&
              clock_ctx.lr == 0x89abcdef && clock_ctx.f1.f64 == 123.25,
              "clock wrapper preserves original output context");
        Check(REX_LOAD_U32(0x82D99128) == previous_delta &&
              REX_LOAD_U32(0x82D9912C) == previous_sum &&
              REX_LOAD_U32(engine_address + 60) == previous_float_sum,
              "clock discard does not publish simulation output prematurely");
        const auto elapsed = REX_LOAD_U32(engine_address + 20) - REX_LOAD_U32(engine_address + 36);
        const auto maximum = REX_LOAD_U32(engine_address + 44);
        const auto actual_dt = elapsed > maximum ? maximum : elapsed;
        Check(actual_dt == dt, "timer receives expected elapsed delta after clock wrapper");
        dt = actual_dt;
    }
    REX_STORE_U8(ctx.r5.u32, dt != 0);
    REX_STORE_U8(ctx.r4.u32, 0);
    REX_STORE_U32(ctx.r6.u32, std::bit_cast<uint32_t>(float(dt) * 0.001f));
    REX_STORE_U8(0x82DE3FB0, 1);
    REX_STORE_U32(0x82D99128, dt);
    if (dt) {
        REX_STORE_U32(engine_address + 32, REX_LOAD_U32(engine_address + 32) + dt);
        REX_STORE_U32(engine_address + 36, REX_LOAD_U32(engine_address + 36) + dt);
        REX_STORE_U32(0x82D9912C, REX_LOAD_U32(0x82D9912C) + dt);
        const auto previous_float = std::bit_cast<float>(REX_LOAD_U32(engine_address + 60));
        REX_STORE_U32(engine_address + 60, std::bit_cast<uint32_t>(previous_float + float(dt) * 0.001f));
    }
    if (mutate_timer_context) {
        ctx.r3.u64 = 0xdead;
        ctx.r5.u64 = 0xbeef;
        ctx.r27.u64 = 0;
        ctx.lr = 0xbad;
    }
}
REX_EXTERN(__imp__sub_823260C8) {
    ++clock_calls;
    REX_STORE_U32(ctx.r3.u32 + 20, clock_sample_ms);
    // Deliberately replace the incoming engine/caller registers: the wrapper
    // must save them before the original and preserve these original outputs.
    ctx.r3.u64 = 0xaabbccddeeff0011ull;
    ctx.r5.u64 = 0x1122334455667788ull;
    ctx.lr = 0x89abcdef;
    ctx.f1.f64 = 123.25;
}
REX_EXTERN(__imp__sub_82325850) {
    ++pause_calls;
    REX_STORE_U8(engine_address + 14, original_pause);
}
REX_EXTERN(__imp__sub_82324938) {
    ++counter_calls;
    // The real original increments only fixed mode's unpaused counter.
    if (REX_LOAD_U8(engine_address + 13) && !REX_LOAD_U8(engine_address + 14))
        REX_STORE_U32(engine_address + 16, REX_LOAD_U32(engine_address + 16) + 1);
}

int main() {
    auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x83000000ull, MEM_RESERVE, PAGE_READWRITE));
    Check(base != nullptr, "reserve sparse guest address space");
    for (const auto page : {0x100000u, 0x110000u, 0x82BA0000u, 0x82CF0000u, 0x82D90000u, 0x82DE0000u})
        Check(VirtualAlloc(base + page, 0x10000, MEM_COMMIT, PAGE_READWRITE) != nullptr, "commit guest page");
    Check(crackdown::experiments::NativeFrameRate() == 0, "original default");
    for (auto target : {60u, 120u, 144u, 240u, 0u}) Select(target);
    for (const auto invalid : {"-1", "30", "61", "1000", "garbage"}) {
        Check(!rex::cvar::SetFlagByName("native_frame_rate", invalid), "reject invalid target");
        Check(crackdown::experiments::NativeFrameRate() == 0, "invalid target retains default");
    }

    ResetEngine(base);
    incoming = TimerContext();
    auto ctx = incoming;
    Queue({33});
    sub_823263D8(ctx, base);
    Check(timer_calls == 1 && REX_LOAD_U8(engine_address + 13) == 1 &&
          REX_LOAD_U32(engine_address + 40) == 33 && REX_LOAD_U32(0x82BAA330) == 32,
          "disabled timer preserves configuration and calls original once");
    Select(240);
    ResetEngine(base);
    incoming = TimerContext(); incoming.lr = 0x12345678;
    ctx = incoming; Queue({33});
    sub_823263D8(ctx, base);
    Check(timer_calls == 2 && REX_LOAD_U8(engine_address + 13) == 1 &&
          REX_LOAD_U32(engine_address + 44) == 33 && REX_LOAD_U32(0x82BAA330) == 32,
          "other caller preserves timer configuration");

    ResetEngine(base);
    expect_native_timer = true; mutate_timer_context = true;
    incoming = TimerContext(); ctx = incoming; Queue({0, 0, 17});
    sub_823263D8(ctx, base);
    Check(delta_index == 3 && REX_LOAD_U8(run_address) == 1, "zero retries eventually publish actual delta");
    Check(REX_LOAD_U32(engine_address + 32) == 17 &&
          REX_LOAD_U32(engine_address + 36) == 517 && REX_LOAD_U32(0x82D99128) == 17,
          "retry retains exactly one real timer commit");
    Check(ctx.r3.u64 == 0xdead, "returns final original context");
    ResetEngine(base);
    incoming = TimerContext(); ctx = incoming; Queue({0, 0, 0, 0});
    sub_823263D8(ctx, base);
    Check(delta_index == 4 && !REX_LOAD_U8(run_address) &&
          REX_LOAD_U32(engine_address + 32) == 0 && REX_LOAD_U32(engine_address + 36) == 500,
          "retry bounded at three and does not invent a commit");

    ctx = {};
    ctx.lr = 0x826A7774; ctx.f1.f64 = 99;
    REX_STORE_U32(0x82D99128, 17);
    REX_STORE_U32(0x82CFE4B4, std::bit_cast<uint32_t>(0.5f));
    crackdown::experiments::PrepareNativeUpdate(ctx, base);
    Check(std::abs(ctx.f1.f64 - 0.0085) < 0.0000001, "main update uses committed delta times caller scale");
    ctx.lr = 0x12345678; ctx.f1.f64 = 99;
    crackdown::experiments::PrepareNativeUpdate(ctx, base);
    Check(ctx.f1.f64 == 99, "other update call unchanged");
    Select(0); ctx.lr = 0x826A7774;
    crackdown::experiments::PrepareNativeUpdate(ctx, base);
    Check(ctx.f1.f64 == 99, "disabled update unchanged");
    Select(240);
    REX_STORE_U32(0x82CFE4B4, std::bit_cast<uint32_t>(0.0f));
    crackdown::experiments::PrepareNativeUpdate(ctx, base);
    Check(ctx.f1.f64 == 0, "zero caller scale stays zero");

    original_pause = 0;
    REX_STORE_U8(0x82DE3FB0, 1); REX_STORE_U32(0x82D99128, 0);
    sub_82325850(ctx, base);
    Check(REX_LOAD_U8(engine_address + 14) == 1, "zero delta guards simulation after original pause work");
    REX_STORE_U32(0x82D99128, 17); sub_82325850(ctx, base);
    Check(REX_LOAD_U8(engine_address + 14) == 0, "positive delta does not force pause");
    original_pause = 1; sub_82325850(ctx, base);
    Check(REX_LOAD_U8(engine_address + 14) == 1, "preserve original pause");
    original_pause = 0; REX_STORE_U8(0x82DE3FB0, 0); REX_STORE_U32(0x82D99128, 0);
    sub_82325850(ctx, base);
    Check(REX_LOAD_U8(engine_address + 14) == 0, "unpublished native clock does not force pause");
    Select(0); REX_STORE_U8(0x82DE3FB0, 1); sub_82325850(ctx, base);
    Check(REX_LOAD_U8(engine_address + 14) == 0 && pause_calls == 5, "disabled pause guard still calls original");

    Select(240);
    ResetEngine(base); REX_STORE_U8(engine_address + 13, 0);
    ctx = {}; ctx.lr = 0x826A7940;
    uint32_t elapsed = 0;
    auto tick = [&](uint32_t delta, bool paused = false) {
        elapsed += delta;
        REX_STORE_U32(engine_address + 32, elapsed);
        REX_STORE_U32(0x82D99128, delta);
        REX_STORE_U8(engine_address + 14, paused);
        sub_82324938(ctx, base);
    };
    for (unsigned i = 0; i < 250; ++i) tick(4);
    Check(REX_LOAD_U32(engine_address + 16) == 30, "one second at 250 updates retains 30 logical ticks");
    tick(100, true);
    Check(REX_LOAD_U32(engine_address + 16) == 30, "paused time does not advance logical counter");
    tick(100);
    Check(REX_LOAD_U32(engine_address + 16) == 31, "hitch advances only one index");
    tick(1); Check(REX_LOAD_U32(engine_address + 16) == 32, "hitch backlog retains next exact index");
    tick(1); Check(REX_LOAD_U32(engine_address + 16) == 33, "hitch backlog drains without skipping indices");
    tick(20);
    const auto before_reset = REX_LOAD_U32(engine_address + 16);
    elapsed = 0; REX_STORE_U32(engine_address + 16, 0);
    tick(1);
    Check(before_reset >= 33 && REX_LOAD_U32(engine_address + 16) == 0, "engine elapsed reset clears fractional backlog");
    tick(33); Check(REX_LOAD_U32(engine_address + 16) == 1, "reset begins fresh logical cadence");
    tick(20);
    REX_STORE_U32(engine_address + 16, 0);
    tick(20);
    Check(REX_LOAD_U32(engine_address + 16) == 0,
          "independent logical counter reset clears old phase with monotonic elapsed clock");
    tick(14);
    Check(REX_LOAD_U32(engine_address + 16) == 1,
          "independent reset counts only new session elapsed time");
    tick(20);
    REX_STORE_U32(engine_address + 16, 0);
    tick(20, true);
    tick(14);
    Check(REX_LOAD_U32(engine_address + 16) == 0,
          "counter reset during pause clears old phase without counting paused time");
    REX_STORE_U32(engine_address + 16, 0xffffffffu);
    tick(20);
    Check(REX_LOAD_U32(engine_address + 16) == 0, "logical counter wraps normally");
    tick(32);
    Check(REX_LOAD_U32(engine_address + 16) == 0, "normal wrap retains fractional cadence");
    tick(1);
    Check(REX_LOAD_U32(engine_address + 16) == 1, "normal wrap does not falsely clear fractional cadence");
    ctx.lr = 0x12345678;
    tick(100); Check(REX_LOAD_U32(engine_address + 16) == 1, "other counter caller unchanged");
    ctx.lr = 0x826A7940;
    REX_STORE_U8(engine_address + 13, 1);
    tick(100); Check(REX_LOAD_U32(engine_address + 16) == 2, "fixed mode leaves increment to original");
    Select(0); REX_STORE_U8(engine_address + 13, 0);
    tick(100); Check(REX_LOAD_U32(engine_address + 16) == 2, "disabled native counter unchanged");
    Check(counter_calls == 269, "counter hook calls original every time");

    Check(rex::cvar::SetFlagByName("native_discard_hitch_time", "true"), "enable hitch policy");
    Select(240);
    timer_samples_clock = true; mutate_timer_context = false; expect_native_timer = true;
    ResetEngine(base); incoming = TimerContext(); ctx = incoming;
    clock_sample_ms = 750; Queue({50});
    sub_823263D8(ctx, base);
    Check(REX_LOAD_U32(engine_address + 36) == 750 &&
          REX_LOAD_U32(engine_address + 32) == 250 && REX_LOAD_U32(engine_address + 64) == 200,
          "actual clock hook drops stall debt through exact wall-time ledgers");
    Check(REX_LOAD_U32(0x82D9912C) == 50 && REX_LOAD_U32(0x82D99128) == 50,
          "actual clock hook leaves only retained simulation time to commit");
    clock_sample_ms = 767; ctx = incoming; Queue({17});
    sub_823263D8(ctx, base);
    Check(REX_LOAD_U32(engine_address + 36) == 767 &&
          REX_LOAD_U32(engine_address + 64) == 200 && REX_LOAD_U32(0x82D9912C) == 67,
          "actual clock hook recovery frame has no old acceleration debt");

    // A direct clock call after the native timer returns must not inherit its
    // thread-local scope, even with the audited clock caller LR.
    ResetEngine(base); auto clock_ctx = TimerContext();clock_ctx.lr = 0x82326430;
    clock_sample_ms = 750; sub_823260C8(clock_ctx, base);
    Check(REX_LOAD_U32(engine_address + 36) == 500 &&
          REX_LOAD_U32(engine_address + 32) == 0 && REX_LOAD_U32(engine_address + 64) == 0,
          "clock discard scope ends when native timer returns");
    Check(clock_ctx.r3.u64 == 0xaabbccddeeff0011ull && clock_ctx.lr == 0x89abcdef,
          "out-of-scope clock preserves original outputs");

    for (unsigned guard = 0; guard < 4; ++guard) {
        ResetEngine(base); incoming = TimerContext(); ctx = incoming;
        REX_STORE_U32(engine_address + 40, 1); REX_STORE_U32(engine_address + 44, 100);
        clock_sample_ms = 750; clock_caller = 0x82326430;
        if (guard == 0) {
            Check(rex::cvar::SetFlagByName("native_discard_hitch_time", "false"), "disable hitch policy for A/B");
        } else if (guard == 1) {
            clock_caller = 0x12345678;
        } else if (guard == 2) {
            REX_STORE_U32(0x82DE3F90, 1);
        } else {
            Select(0); expect_native_timer = false;
        }
        Queue({100}); sub_823263D8(ctx, base);
        Check(REX_LOAD_U32(engine_address + 36) == 600 &&
              REX_LOAD_U32(engine_address + 32) == 100 && REX_LOAD_U32(engine_address + 64) == 0,
              "disabled policy, other caller, replay and target0 preserve original residual behavior");
        Check(rex::cvar::SetFlagByName("native_discard_hitch_time", "true"), "restore hitch policy");
    }
    Select(240); expect_native_timer = false; clock_caller = 0x82326430;
    ResetEngine(base); incoming = TimerContext(); incoming.lr = 0x12345678; ctx = incoming;
    REX_STORE_U32(engine_address + 44, 100); clock_sample_ms = 750;
    Queue({100}); sub_823263D8(ctx, base);
    Check(REX_LOAD_U32(engine_address + 64) == 0 && REX_LOAD_U32(engine_address + 36) == 600,
          "other timer caller never activates native clock scope");
    Check(clock_calls == 8, "every clock hook dispatches original exactly once");
    VirtualFree(base, 0, MEM_RELEASE);
    std::printf("Native timing hook tests passed (%u checks)\n", checks);
}
