#include "graphics_options.h"

#include <rex/cvar.h>
#include <rex/graphics/command_processor.h>
#include <rex/graphics/graphics_system.h>
#include <rex/logging.h>
#include <rex/ui/window.h>

REXCVAR_DEFINE_BOOL(show_fps, false, "Game Enhancements", "Show the guest FPS counter without the performance graph (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(disable_bloom, false, "Game Enhancements", "Disable bloom independently of shadows (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(disable_shadows, false, "Game Enhancements", "Disable shadows independently of bloom (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_STRING(postprocess_antialiasing, "none", "Game Enhancements", "Final image anti-aliasing: none, fxaa, or fxaa_extreme (restart required).")
    .allowed({"none", "fxaa", "fxaa_extreme"})
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(fullscreen, false, "UI/Window", "Start the game in borderless fullscreen (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

void ApplyGraphicsOptions(rex::graphics::GraphicsSystem* graphics, uint8_t* membase) {
    if (REXCVAR_GET(show_fps)) {
        membase[0x82DE4F14] = 1;
        membase[0x82DE25A9] = 1;
    }
    // TU0's render path branches to the bloom pass when this byte is nonzero.
    if (REXCVAR_GET(disable_bloom)) membase[0x82BAA3AA] = 0;
    if (REXCVAR_GET(disable_shadows)) membase[0x82DE25B3] = 0;

    if (graphics && graphics->command_processor()) {
        using Effect = rex::graphics::CommandProcessor::SwapPostEffect;
        const auto& name = REXCVAR_GET(postprocess_antialiasing);
        const auto effect = name == "fxaa" ? Effect::kFxaa :
            name == "fxaa_extreme" ? Effect::kFxaaExtreme : Effect::kNone;
        graphics->command_processor()->SetDesiredSwapPostEffect(effect);
        REXLOG_INFO("Graphics options: anti-aliasing {}, FPS counter {}, disable bloom {}, disable shadows {}",
            name, REXCVAR_GET(show_fps), REXCVAR_GET(disable_bloom), REXCVAR_GET(disable_shadows));
    }
}

void ApplyLaunchWindowOptions(rex::ui::Window* window) {
    if (window) window->SetFullscreen(REXCVAR_GET(fullscreen));
}
