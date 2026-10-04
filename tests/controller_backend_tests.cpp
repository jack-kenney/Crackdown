#include "controller_input.h"
#include <rex/cvar.h>
#include <rex/ui/windowed_app_context_sdl.h>
#include <cstdlib>
#include <iostream>
#include <thread>
static void Require(bool value, const char* message) {
    if (!value) { std::cerr << message << '\n'; std::abort(); }
}
using rex::X_RESULT;
int main(int argc, char** argv) {
    const bool xinput = argc > 1 && std::string_view(argv[1]) == "true";
    rex::cvar::SetFlagByName("sdl_direct_xinput", xinput ? "true" : "false");
    rex::ui::SDLWindowedAppContext context;
    Require(context.Initialize(), "SDL context setup failed");
    auto window = rex::ui::Window::Create(context, "Controller regression", 320, 240);
    Require(bool(window), "window creation failed");
    auto owner = CreateControllerInputSystem(false);
    auto* input = static_cast<rex::input::InputSystem*>(owner.get());
    Require(rex::cvar::GetFlagByName("input_backend") == (xinput ? "xinput" : "sdl"), "backend selection failed");
    input->AttachWindow(window.get());
    window->Open();
    // SDL now lives inside rexruntime.dll. A host-linked static SDL virtual pad
    // belongs to a different SDL instance and cannot test the runtime driver.
    // Exercise the production factory and guest queries while its UI loop runs.
    // With hardware connected this includes the real pad; otherwise the idle
    // player-one fallback. Full button/axis behavior needs a gameplay check.
    std::jthread guest([&] {
        for (int i = 0; i < 2000; ++i) {
            rex::input::X_INPUT_STATE state{};
            rex::input::X_INPUT_CAPABILITIES caps{};
            rex::input::X_INPUT_VIBRATION vibration{};
            Require(input->GetState(0, &state) == 0, "player-one query failed");
            Require(input->GetCapabilities(0, 0, &caps) == 0, "capabilities query failed");
            input->SetState(0, &vibration);
        }
        context.CallInUIThread([&] { window->RequestClose(); context.QuitFromUIThread(); });
    });
    context.RunMainMessageLoop();
    guest.join();
    // The SDK's window close notification unregisters its SDL event watch.
    window.reset();
    owner.reset();
    std::cout << (xinput ? "Native XInput" : "SDL3") << " factory, UI loop and concurrent guest queries passed\n";
}
