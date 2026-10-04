#pragma once

#include <rex/input/input_driver.h>
#include <rex/input/input_system.h>

// SDL posts controller events while holding its joystick lock. The SDK event
// watch then takes the controller-state lock. Acquire the joystick lock before
// entering the SDK driver so rumble and capability queries use the same order.
class OrderedControllerInput final : public rex::input::InputDriver {
public:
    explicit OrderedControllerInput(std::unique_ptr<rex::input::InputDriver> driver);
    rex::X_STATUS Setup() override;
    rex::X_RESULT GetCapabilities(uint32_t user, uint32_t flags, rex::input::X_INPUT_CAPABILITIES* caps) override;
    rex::X_RESULT GetState(uint32_t user, rex::input::X_INPUT_STATE* state) override;
    rex::X_RESULT SetState(uint32_t user, rex::input::X_INPUT_VIBRATION* vibration) override;
    rex::X_RESULT GetKeystroke(uint32_t user, uint32_t flags, rex::input::X_INPUT_KEYSTROKE* key) override;
private:
    std::unique_ptr<rex::input::InputDriver> driver_;
};

std::unique_ptr<rex::system::IInputSystem> CreateControllerInputSystem(bool tool_mode);
