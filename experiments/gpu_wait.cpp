// Optional host backoff for the game's profiled GPU progress polling loop.
#include <cstdint>

namespace crackdown::experiments::gpu_wait {
constexpr uint32_t kPollCaller = 0x826BB590;
constexpr uint32_t kPollsBeforeBackoff = 256;
enum class Action { None, Pause, Backoff };

constexpr bool RingWaitPending(uint32_t write, uint32_t target, uint32_t read) {
    // Preserve the title's unsigned comparisons, including counter wraparound.
    return uint32_t(write - target) < uint32_t(write - read);
}

class PollPolicy {
public:
    Action Poll(bool enabled, uint32_t caller, uint32_t device,
                uint32_t target, bool pending) {
        if (!enabled || caller != kPollCaller || !device || !pending) {
            polls_ = 0;
            return Action::None;
        }
        if (device != device_ || target != target_) {
            polls_ = 0;
            device_ = device;
            target_ = target;
        }
        if (++polls_ < kPollsBeforeBackoff) return Action::Pause;
        polls_ = 0;
        return Action::Backoff;
    }

private:
    uint32_t polls_ = 0, device_ = 0, target_ = 0;
};
}  // namespace crackdown::experiments::gpu_wait

// The standalone regression test includes only the deterministic policy above.
#ifndef CRACKDOWN_GPU_WAIT_POLICY_TEST
#ifndef CRACKDOWN_GPU_WAIT_HOOK_TEST
#include "generated/crackdown_pch.h"
#include <rex/cvar.h>
#include <immintrin.h>
#include <thread>
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#endif  // CRACKDOWN_GPU_WAIT_HOOK_TEST

REXCVAR_DEFINE_BOOL(pace_gpu_wait, false, "Experiments",
    "Back off the profiled guest GPU progress poll after 256 unsuccessful checks.");

namespace {
using namespace crackdown::experiments::gpu_wait;
class BackoffTimer {
public:
#if defined(_WIN32) && !defined(CRACKDOWN_GPU_WAIT_HOOK_TEST)
    BackoffTimer() : timer_(CreateWaitableTimerExW(nullptr, nullptr,
        CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE)) {}
    ~BackoffTimer() { if (timer_) CloseHandle(timer_); }
#else
    BackoffTimer() = default;
#endif
    BackoffTimer(const BackoffTimer&) = delete;
    BackoffTimer& operator=(const BackoffTimer&) = delete;
    void Wait() {
#ifdef CRACKDOWN_GPU_WAIT_HOOK_TEST
        TestBackoffWait();
        return;
#elif defined(_WIN32)
        LARGE_INTEGER due;
        due.QuadPart = -500;  // 50 microseconds in 100 ns units.
        if (timer_ && SetWaitableTimer(timer_, &due, 0, nullptr, nullptr, FALSE)) {
            WaitForSingleObject(timer_, INFINITE);
            return;
        }
#endif
        // Do not substitute a coarse millisecond sleep into a GPU fence poll.
        std::this_thread::yield();
    }
private:
#if defined(_WIN32) && !defined(CRACKDOWN_GPU_WAIT_HOOK_TEST)
    HANDLE timer_ = nullptr;
#endif
};
}  // namespace

REX_EXTERN(__imp__sub_826BBEE0);
REX_EXTERN(sub_826BBEE0) {
    using namespace crackdown::experiments::gpu_wait;
    const uint32_t caller = uint32_t(ctx.lr);
    if (!REXCVAR_GET(pace_gpu_wait) || caller != kPollCaller) {
        __imp__sub_826BBEE0(ctx, base);
        return;
    }
    // The original helper clobbers r3 and may clobber volatile registers.
    // r3 points to the poll descriptor; descriptor[0] is the D3D device.
    // In caller 826BB4F0, r30 holds the requested GPU progress position.
    const uint32_t device = REX_LOAD_U32(ctx.r3.u32);
    const uint32_t target = ctx.r30.u32;
    __imp__sub_826BBEE0(ctx, base);
    thread_local PollPolicy policy;
    bool pending = false;
    if (ctx.r3.u32 == 1 && device) {
        const uint32_t read_pointer = REX_LOAD_U32(device + 10768);
        if (read_pointer) {
            const uint32_t write = REX_LOAD_U32(device + 10780);
            const uint32_t read = REX_LOAD_U32(read_pointer);
            pending = RingWaitPending(write, target, read);
        }
    }
    const Action action = policy.Poll(true, caller, device, target, pending);
    if (action != Action::None) _mm_pause();
    if (action == Action::Backoff) {
        thread_local BackoffTimer timer;
        timer.Wait();
    }
    // Keep the original return value and every guest register unchanged.
}
#endif  // CRACKDOWN_GPU_WAIT_POLICY_TEST
