#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <thread>

// Compile the actual hook with a bounded in-memory guest and injected host waits.
// This checks call ordering and register preservation without SDK or real delays.
struct TestRegister {
    union { uint64_t u64; uint32_t u32; };
};
struct TestContext {
    TestRegister r3{}, r30{}, r5{};
    uint64_t lr = 0;
};
static bool enabled = false, complete_during_original = false;
static unsigned original_calls = 0, pauses = 0, waits = 0;
static std::map<uint32_t, uint32_t> memory;
static TestContext original_output;
uint32_t TestRead(uint32_t address) { return memory.at(address); }
void TestBackoffWait() { ++waits; }
void TestPause() { ++pauses; }
void __imp__sub_826BBEE0(TestContext& ctx, uint8_t*) {
    ++original_calls;
    if (complete_during_original) memory[500] = 80;
    ctx.r3.u64 = 1;
    ctx.r30.u64 = 0x12345678;  // The wrapper must use the pre-call target.
    ctx.r5.u64 = 0xfedcba9876543210ULL;
    ctx.lr = 0xaabbccdd;
    original_output = ctx;
}
#define CRACKDOWN_GPU_WAIT_HOOK_TEST
#define REXCVAR_DEFINE_BOOL(...)
#define REXCVAR_GET(name) enabled
#define REX_EXTERN(name) void name(TestContext& ctx, uint8_t* base)
#define REX_LOAD_U32(address) TestRead(address)
#define _mm_pause() TestPause()
#include "../experiments/gpu_wait.cpp"

namespace {
void Check(bool condition, const char* message) {
    if (!condition) {
        std::fprintf(stderr, "GPU wait policy failed: %s\n", message);
        std::exit(1);
    }
}
}

int main() {
    using namespace crackdown::experiments::gpu_wait;
    Check(RingWaitPending(100, 80, 50), "unfinished progress must wait");
    Check(!RingWaitPending(100, 80, 80), "equal progress must not wait");
    Check(!RingWaitPending(100, 80, 90), "advanced progress must not wait");
    Check(RingWaitPending(10, 5, 0xfffffff0), "unfinished wrapped counter must wait");
    Check(!RingWaitPending(10, 0xfffffff0, 5), "completed wrapped counter must not wait");
    PollPolicy policy;
    for (unsigned i = 0; i < 1024; ++i) {
        Check(policy.Poll(false, kPollCaller, 1, 2, true) == Action::None,
              "disabled mode must never pause or wait");
        Check(policy.Poll(true, kPollCaller + 4, 1, 2, true) == Action::None,
              "other callers must never pause or wait");
        Check(policy.Poll(true, kPollCaller, 1, 2, false) == Action::None,
              "observed GPU progress must never pause or wait");
    }
    Check(policy.Poll(true, kPollCaller, 0, 2, true) == Action::None,
          "missing device must not wait");
    for (unsigned cycle = 0; cycle < 3; ++cycle) {
        for (unsigned i = 1; i < kPollsBeforeBackoff; ++i)
            Check(policy.Poll(true, kPollCaller, 1, 2, true) == Action::Pause,
                  "short polls get only a pause hint");
        Check(policy.Poll(true, kPollCaller, 1, 2, true) == Action::Backoff,
              "every 256th pending poll backs off");
    }
    for (unsigned i = 0; i < 255; ++i)
        policy.Poll(true, kPollCaller, 1, 2, true);
    Check(policy.Poll(true, kPollCaller, 1, 2, false) == Action::None,
          "progress immediately before threshold suppresses wait");
    Check(policy.Poll(true, kPollCaller, 1, 2, true) == Action::Pause,
          "progress resets poll budget");
    for (unsigned i = 0; i < 254; ++i)
        policy.Poll(true, kPollCaller, 1, 2, true);
    Check(policy.Poll(true, kPollCaller, 2, 2, true) == Action::Pause,
          "new device resets poll budget");
    for (unsigned i = 0; i < 254; ++i)
        policy.Poll(true, kPollCaller, 2, 2, true);
    Check(policy.Poll(true, kPollCaller, 2, 3, true) == Action::Pause,
          "new target resets poll budget");

    auto invoke = [&](uint32_t caller) {
        TestContext ctx;
        ctx.r3.u64 = 100;
        ctx.r30.u64 = 80;
        ctx.lr = caller;
        sub_826BBEE0(ctx, nullptr);
        Check(ctx.r3.u64 == original_output.r3.u64 &&
              ctx.r30.u64 == original_output.r30.u64 &&
              ctx.r5.u64 == original_output.r5.u64 && ctx.lr == original_output.lr,
              "actual hook must preserve original outputs and registers");
    };
    // No guest-memory access is permitted in the default-off or other-caller path.
    memory.clear();
    invoke(kPollCaller);
    enabled = true;
    invoke(kPollCaller + 4);
    Check(original_calls == 2 && pauses == 0 && waits == 0,
          "disabled/other-caller hook must only forward the original");
    memory = {{100, 1000}, {1000 + 10768, 500}, {1000 + 10780, 100}, {500, 50}};
    for (unsigned i = 0; i < 255; ++i) invoke(kPollCaller);
    Check(pauses == 255 && waits == 0, "actual hook must not wait below threshold");
    complete_during_original = true;
    invoke(kPollCaller);
    Check(pauses == 255 && waits == 0,
          "fresh progress observed after original suppresses threshold wait");
    complete_during_original = false;
    memory[500] = 50;
    for (unsigned i = 0; i < 256; ++i) invoke(kPollCaller);
    Check(pauses == 511 && waits == 1,
          "actual hook waits only after 256 fresh unsuccessful polls");
    std::puts("GPU wait policy tests passed");
}
