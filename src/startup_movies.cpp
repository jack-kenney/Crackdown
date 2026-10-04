#include <rex/cvar.h>
#include <rex/logging.h>
#include "crackdown_pch.h"

REXCVAR_DEFINE_BOOL(skip_intro_movies, true, "Game Enhancements",
    "Skip the Microsoft and Realtime Worlds startup movies (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

extern "C" {
    REX_FUNC(__imp__sub_826543A0);
    REX_FUNC(__imp__sub_82653EF0);
}

// TU0's startup screen opens MSGS.bik in state 4 and RTW_Logo.bik in
// state 5. Use its normal completion handler before opening either movie:
// it frees the preloaded data and advances the sequence, retaining the UI
// reset and frontend initialization performed by the guest. State 5 may
// re-enter this hook while state 4 is completing. Other startup states and
// every campaign/dossier movie use the original path.
REX_EXTERN(sub_826543A0) {
    const auto screen = ctx.r3.u32;
    if (REXCVAR_GET(skip_intro_movies) && screen) {
        const auto state = REX_LOAD_U32(screen + 272);
        if (state == 4 || state == 5) {
            REXLOG_INFO("Skipping startup movie {}", state == 4 ? "MSGS.bik" : "RTW_Logo.bik");
            __imp__sub_82653EF0(ctx, base);
            return;
        }
    }
    __imp__sub_826543A0(ctx, base);
}
