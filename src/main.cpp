// Crackdown TU0 host integration for ReXGlue 0.10.

#include "generated/crackdown_init.h"

#include <rex/rex_app.h>
#include <rex/filesystem/devices/host_path_device.h>
#include <rex/system/interfaces/graphics.h>

#include "cache.h"
#include "controller_input.h"
#include "graphics_options.h"
#ifdef _WIN32
#include "automation.h"
#include "timer_resolution.h"
#endif

REXCVAR_DEFINE_BOOL(fix_lighting, false, "Game Enhancements", "Fix lighting calculation; improves visibility and color accuracy at a substantial performance cost (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(fix_light_occlusion, true, "Game Enhancements", "Disable light coronas to prevent halos appearing through walls (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(misc_performance_improvements, false, "Game Enhancements", "Disable bloom and shadows to potentially improve performance (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(show_perfgraph, false, "Game Enhancements", "Show the guest FPS counter and performance graph (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
#ifdef _WIN32
REXCVAR_DEFINE_BOOL(automation, false, "Debug", "Enable local automated controller input and guest frame capture for testing.");
REXCVAR_DEFINE_BOOL(high_resolution_timer, true, "Performance", "Request 1 ms Windows timer precision for runtime waits (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
#endif

class CrackdownApp : public rex::ReXApp {
public:
    using rex::ReXApp::ReXApp;

    static std::unique_ptr<rex::ui::WindowedApp> Create(
        rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<CrackdownApp>(new CrackdownApp(ctx, "crackdown",
        PPCImageConfig));
    }
  
    void OnPreSetup(rex::RuntimeConfig& config) override
    {
        if (config.gpu_plugin.empty()) config.gpu_plugin = "xenos";
        config.input_factory = CreateControllerInputSystem;
#ifdef _WIN32
        if (REXCVAR_GET(high_resolution_timer)) {
            timer_resolution_ = std::make_unique<WindowsTimerResolution>();
            if (timer_resolution_->active()) {
                REXLOG_INFO("Windows 1 ms timer precision enabled");
            } else {
                REXLOG_WARN("Windows 1 ms timer request failed; runtime waits may be slower");
            }
        }
        if (REXCVAR_GET(automation)) {
            automation_ = std::make_unique<AutomationSession>();
            config.input_factory = [this](bool) { return automation_->CreateInputSystem(); };
        }
#endif
    }

    void OnPostSetup() override
    {
        if (REXCVAR_GET(fix_lighting))
        {
            // Fix lighting (https://github.com/xenia-canary/game-compatibility/issues/102#issuecomment-2385029628)
            rex::cvar::SetFlagByName("d3d12_readback_resolve", "true");
        }

        // Fix invalid fetch constant type error
        rex::cvar::SetFlagByName("gpu_allow_invalid_fetch_constants", "true");

        // Fix infinite loading screen (https://github.com/xenia-canary/game-compatibility/issues/102#issuecomment-1222126375)
        auto file_system = runtime()->file_system();

        auto cache_device = std::make_unique<CacheDevice>("\\CACHE");
        if (!cache_device->Initialize()) {
            REXLOG_ERROR("Unable to initialize cache device.");
        }
        else {
            if (!file_system->RegisterDevice(std::move(cache_device))) {
                REXLOG_ERROR("Unable to register cache device.");
            }
            else {
                file_system->RegisterSymbolicLink("cache:", "\\CACHE");
            }
        }

        // Below memory writes are originally made by Adrian (https://github.com/xenia-canary/game-patches/blob/main/patches/4D5307DC%20-%20Crackdown%20%28TU0%29.patch.toml)
        auto membase = rex::system::kernel_state()->memory()->virtual_membase();

        if (REXCVAR_GET(fix_light_occlusion))
        {
            // Fix seeing lights through walls by disabling them.
            *reinterpret_cast<bool*>(membase + 0x82BAA3AD) = false; // "togglegloballights" command.
        }

        if (REXCVAR_GET(misc_performance_improvements))
        {
            // Performance-improving commands
            *reinterpret_cast<bool*>(membase + 0x82BAA3AA) = false; // "togglebloom" command: zero skips the bloom pass.
            *reinterpret_cast<bool*>(membase + 0x82DE25B3) = false; // Disable shadows.
        }
        ApplyGraphicsOptions(membase);
        StartAutomationCapture();
    }

    void StartAutomationCapture()
    {
#ifdef _WIN32
        if (automation_) {
            auto* graphics = runtime()->graphics_system();
            automation_->StartCapture(graphics ? graphics->presenter() : nullptr, user_data_root().parent_path() / "automation" / std::to_string(GetCurrentProcessId()));
        }
#endif

    }

    void OnShutdown() override
    {
#ifdef _WIN32
        if (automation_) automation_->StopCapture();
#endif
    }

#ifdef _WIN32
    std::unique_ptr<WindowsTimerResolution> timer_resolution_;
    std::unique_ptr<AutomationSession> automation_;
#endif
};

REX_DEFINE_APP(crackdown, CrackdownApp::Create)

// Match the SDK's no-op semantics, but avoid thousands of warnings per minute
// from TU0's audio workers. Guest FP state already lives in PPCContext.
REX_EXTERN(__imp__KeSaveFloatingPointState) {}
REX_EXTERN(__imp__KeRestoreFloatingPointState) {}
