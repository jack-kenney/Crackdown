#include "experiments/frame_pacing.h"

#include <iostream>
#include <stdexcept>

using crackdown::experiments::FrameClock;
using crackdown::experiments::FramePacer;
using crackdown::experiments::NextFrameDeadline;
using namespace std::chrono_literals;

void Require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

int main() {
    try {
        const auto epoch = FrameClock::time_point(100s);
        for (unsigned fps : {60u, 120u, 144u, 240u}) {
            const auto period = FramePacer::PeriodForFps(fps);
            const auto ideal = std::chrono::duration<double>(1.0 / fps);
            Require(period > FrameClock::duration::zero(), "Frame period must be positive.");
            Require(ideal - period >= std::chrono::duration<double>::zero() &&
                        ideal - period < FrameClock::duration(1),
                    "Fractional frame period lost more than one clock tick.");
            FramePacer pacer;
            Require(pacer.Deadline(epoch, period) == epoch, "First frame must not wait.");

            // Variable amounts of frame work and small oversleeps must not
            // shift the schedule by that oversleep on every frame.
            auto previous = epoch;
            for (unsigned frame = 1; frame <= 100; ++frame) {
                const auto work = period / (frame % 3 + 2);
                const auto now = previous + work + 100us;
                const auto expected = epoch + period * frame;
                Require(pacer.Deadline(now, period) == expected,
                        "Variable work or slight oversleep shifted the cadence.");
                previous = expected;
            }

            const auto due = previous + period;
            Require(pacer.Deadline(due + 100us, period) == due,
                    "Slightly late frame must preserve its scheduled deadline.");
            Require(pacer.Deadline(due + 100us + period / 4, period) == due + period,
                    "Frame after slight oversleep must recover its cadence.");

            const auto stalled = due + period * 10 + period / 3;
            Require(pacer.Deadline(stalled, period) == stalled,
                    "Long stall must establish a new epoch.");
            Require(pacer.Deadline(stalled + 100us, period) == stalled + period,
                    "Frame after stall must wait instead of catching up in a burst.");

            pacer.Reset();
            Require(pacer.Deadline(stalled, period) == stalled, "Reset retained an old deadline.");
            Require(pacer.Deadline(stalled + 100us, period / 2) == stalled + 100us,
                    "Changing target must establish a new epoch.");
            Require(pacer.Deadline(stalled + 200us, FrameClock::duration::zero()) == stalled + 200us,
                    "Uncapped mode must not wait.");
        }
        const auto period = FramePacer::PeriodForFps(60);
        Require(NextFrameDeadline(epoch + 2 * period, epoch, period) == epoch + 2 * period,
                "A fully missed additional period must reset immediately.");
        Require(NextFrameDeadline(epoch, epoch - 1s, -period) == epoch,
                "Nonpositive periods must never introduce a delay.");
        Require(FramePacer::PeriodForFps(0) == FrameClock::duration::zero(),
                "Zero FPS target must mean uncapped.");
        std::cout << "Frame pacing: targets, variable work, oversleep, stalls and reset passed.\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
