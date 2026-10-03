#include "timer_resolution.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <timeapi.h>

WindowsTimerResolution::WindowsTimerResolution()
    : active_(timeBeginPeriod(1) == TIMERR_NOERROR) {}

WindowsTimerResolution::~WindowsTimerResolution() {
    if (active_) timeEndPeriod(1);
}
