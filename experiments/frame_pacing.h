#pragma once

#include <chrono>
#include <optional>

namespace crackdown::experiments {

using FrameClock = std::chrono::steady_clock;

// Pure deadline policy. A slightly late frame retains the original cadence.
// Missing an entire additional period establishes a new epoch, preventing a
// sequence of immediately due frames after a stall. The caller may pass the
// returned deadline directly to std::this_thread::sleep_until.
inline FrameClock::time_point NextFrameDeadline(
    FrameClock::time_point now,
    std::optional<FrameClock::time_point> previous_deadline,
    FrameClock::duration period) {
    if (!previous_deadline || period <= FrameClock::duration::zero()) {
        return now;
    }
    const auto deadline = *previous_deadline + period;
    if (now > deadline && now - deadline >= period) {
        return now;
    }
    return deadline;
}

class FramePacer {
public:
    // Zero represents uncapped rendering. Conversion loses at most one clock
    // tick per frame, including for rates such as 144 Hz with fractional ms.
    static FrameClock::duration PeriodForFps(unsigned fps) {
        return fps ? std::chrono::duration_cast<FrameClock::duration>(
                         std::chrono::duration<double>(1.0 / fps))
                   : FrameClock::duration::zero();
    }

    void Reset() {
        deadline_.reset();
        period_ = FrameClock::duration::zero();
    }

    // Call once per frame. The first frame (also after Reset or a target
    // change) establishes the epoch and does not delay. Retain this object
    // between calls and sleep_until its result; do not spin or sleep_for a
    // whole period after the frame's work.
    FrameClock::time_point Deadline(FrameClock::time_point now,
                                    FrameClock::duration period) {
        if (period != period_) Reset();
        period_ = period;
        deadline_ = NextFrameDeadline(now, deadline_, period);
        return *deadline_;
    }

private:
    std::optional<FrameClock::time_point> deadline_;
    FrameClock::duration period_ = FrameClock::duration::zero();
};

} // namespace crackdown::experiments
