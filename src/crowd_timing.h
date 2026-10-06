#pragma once
#include <rex/ppc.h>

namespace crackdown {
// Set by the native main clock; original-timing builds never enable it.
void SetCrowdNativeTiming(bool enabled);
bool NativeTimingActive();
}

// Register helpers are also exercised directly by deterministic fixtures.
struct CrackdownCrowdHooks {
    uint8_t* base;
    void Advance(const PPCRegister& object, PPCVRegister& displacement) const;
    void Countdown(const PPCRegister& object, PPCRegister& remaining) const;
    void Animation(const PPCRegister& object, PPCRegister& interval) const;
    void Steering(PPCRegister& gain) const;
};
