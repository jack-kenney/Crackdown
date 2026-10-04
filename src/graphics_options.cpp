#include "graphics_options.h"
#include "crackdown_pch.h"

#include <rex/cvar.h>
#include <rex/logging.h>

REXCVAR_DEFINE_BOOL(show_fps, false, "Game Enhancements", "Show the guest FPS counter without the performance graph (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(disable_bloom, false, "Game Enhancements", "Disable bloom independently of shadows (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(disable_shadows, false, "Game Enhancements", "Disable shadows independently of bloom (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

void ApplyGraphicsOptions(uint8_t* membase) {
    if (REXCVAR_GET(disable_bloom)) membase[0x82BAA3AA] = 0;
    if (REXCVAR_GET(disable_shadows)) membase[0x82DE25B3] = 0;
}

REXCVAR_DECLARE(bool, show_perfgraph);
REX_EXTERN(__imp__sub_826A7AD0);

REX_EXTERN(sub_826A7AD0) {
    // TU0 can enter this diagnostic drawing function before its shader exists.
    // Enable the requested overlays on the render thread once that dependency
    // is ready, and suppress them again if it is released during a transition.
    if (REXCVAR_GET(show_fps) || REXCVAR_GET(show_perfgraph)) {
        const bool ready = REX_LOAD_U32(0x82D1AEDC) != 0;
        REX_STORE_U32(0x82DE4F14, ready ? 1 : 0);
        REX_STORE_U8(0x82DE4D1A, ready && REXCVAR_GET(show_perfgraph));
        REX_STORE_U8(0x82DE25A9, 1);
    }
    __imp__sub_826A7AD0(ctx, base);
}
