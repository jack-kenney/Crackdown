#include "controller_input.h"
#include <rex/cvar.h>

// Compatibility for saved launcher settings and older command lines.
REXCVAR_DEFINE_BOOL(sdl_direct_xinput, false, "Input", "Use native XInput instead of SDL (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

std::unique_ptr<rex::system::IInputSystem> CreateControllerInputSystem(bool tool_mode) {
    if (REXCVAR_GET(sdl_direct_xinput)) rex::cvar::SetFlagByName("input_backend", "xinput");
    // SDK 0.10 queues SDL event callbacks before taking its controller lock.
    return rex::input::CreateDefaultInputSystem(tool_mode);
}
