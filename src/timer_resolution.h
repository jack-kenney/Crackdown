#pragma once

// Keep the request alive through runtime shutdown. Windows timer requests are
// process-local, so another application's request does not fix our Sleep calls.
class WindowsTimerResolution final {
public:
    WindowsTimerResolution();
    ~WindowsTimerResolution();
    WindowsTimerResolution(const WindowsTimerResolution&) = delete;
    WindowsTimerResolution& operator=(const WindowsTimerResolution&) = delete;

    bool active() const { return active_; }

private:
    bool active_;
};
