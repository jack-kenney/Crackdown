#include "controller_input.h"

#include <SDL.h>
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/input/nop/nop_input_driver.h>
#include <rex/input/sdl/sdl_input_driver.h>

using rex::X_STATUS;

#ifdef _WIN32
REXCVAR_DEFINE_BOOL(sdl_direct_xinput, false, "Input", "Poll Xbox controllers through SDL's XInput backend instead of Windows Raw Input (restart required).")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
#endif

namespace {
class JoystickLock {
public:
    JoystickLock() { SDL_LockJoysticks(); }
    ~JoystickLock() { SDL_UnlockJoysticks(); }
    JoystickLock(const JoystickLock&) = delete;
    JoystickLock& operator=(const JoystickLock&) = delete;
};
}

OrderedControllerInput::OrderedControllerInput(std::unique_ptr<rex::input::InputDriver> driver)
    : InputDriver(nullptr, 0), driver_(std::move(driver)) {
    driver_->set_is_active_callback([this] { return is_active(); });
}

rex::X_STATUS OrderedControllerInput::Setup() {
    // SDL initializes its joystick lock during subsystem setup.
    return driver_->Setup();
}

rex::X_RESULT OrderedControllerInput::GetCapabilities(uint32_t user, uint32_t flags, rex::input::X_INPUT_CAPABILITIES* caps) {
    JoystickLock lock;
    return driver_->GetCapabilities(user, flags, caps);
}

rex::X_RESULT OrderedControllerInput::GetState(uint32_t user, rex::input::X_INPUT_STATE* state) {
    JoystickLock lock;
    return driver_->GetState(user, state);
}

rex::X_RESULT OrderedControllerInput::SetState(uint32_t user, rex::input::X_INPUT_VIBRATION* vibration) {
    JoystickLock lock;
    return driver_->SetState(user, vibration);
}

rex::X_RESULT OrderedControllerInput::GetKeystroke(uint32_t user, uint32_t flags, rex::input::X_INPUT_KEYSTROKE* key) {
    JoystickLock lock;
    return driver_->GetKeystroke(user, flags, key);
}

std::unique_ptr<rex::system::IInputSystem> CreateControllerInputSystem(bool tool_mode) {
    auto input = std::make_unique<rex::input::InputSystem>(nullptr);
    if (!tool_mode) {
#ifdef _WIN32
        if (REXCVAR_GET(sdl_direct_xinput)) {
            // Raw Input arrives through a Windows message queue. The XInput
            // backend reads the latest Xbox pad state during GetState instead.
            // Keep SDL's mapping, menu keystrokes and rumble implementation.
            SDL_SetHintWithPriority(SDL_HINT_JOYSTICK_RAWINPUT, "0", SDL_HINT_OVERRIDE);
            REXLOG_INFO("SDL direct Xbox polling requested: Windows Raw Input disabled");
        }
#endif
        auto driver = std::make_unique<OrderedControllerInput>(
            std::make_unique<rex::input::sdl::SDLInputDriver>(nullptr, 0));
        if (driver->Setup() == X_STATUS_SUCCESS) input->AddDriver(std::move(driver));
    }
    input->AddDriver(std::make_unique<rex::input::nop::NopInputDriver>(nullptr, tool_mode ? 0 : 1));
    return input;
}
