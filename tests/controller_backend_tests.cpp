#define SDL_MAIN_HANDLED
#include "controller_input.h"
#include <SDL.h>
#include <rex/cvar.h>
#include <cstdlib>
#include <iostream>

static void Require(bool value, const char* message) {
    if (!value) { std::cerr << message << '\n'; std::abort(); }
}
static int rumble_calls;
using rex::X_RESULT;
static int Rumble(void*, Uint16 low, Uint16 high) {
    if (low == 12000 && high == 34000) ++rumble_calls;
    return 0;
}
int main(int argc, char** argv) {
    if (argc > 1) rex::cvar::SetFlagByName("sdl_direct_xinput", argv[1]);
    auto owner = CreateControllerInputSystem(false);
    auto* input = static_cast<rex::input::InputSystem*>(owner.get());
    for (int device = 0; device < SDL_NumJoysticks(); ++device) {
        auto guid = SDL_JoystickGetDeviceGUID(device);
        char text[33]; SDL_JoystickGetGUIDString(guid, text, sizeof(text));
        std::cout << "Controller " << SDL_JoystickNameForIndex(device) << ": GUID " << text
                  << ", driver tag " << char(guid.data[14]) << '\n';
    }
    SDL_VirtualJoystickDesc desc{};
    desc.version = SDL_VIRTUAL_JOYSTICK_DESC_VERSION;
    desc.type = SDL_JOYSTICK_TYPE_GAMECONTROLLER;
    desc.naxes = SDL_CONTROLLER_AXIS_MAX;
    desc.nbuttons = SDL_CONTROLLER_BUTTON_MAX;
    desc.axis_mask = (1u << SDL_CONTROLLER_AXIS_MAX) - 1;
    desc.button_mask = (1u << SDL_CONTROLLER_BUTTON_MAX) - 1;
    desc.name = "Crackdown controller regression";
    desc.Rumble = Rumble;
    int device = SDL_JoystickAttachVirtualEx(&desc);
    Require(device >= 0, "virtual controller attach failed");
    SDL_PumpEvents();
    auto* pad = SDL_GameControllerFromInstanceID(SDL_JoystickGetDeviceInstanceID(device));
    Require(pad != nullptr, "SDK did not open virtual controller");
    int user = SDL_GameControllerGetPlayerIndex(pad);
    Require(user >= 0 && user < 4, "SDK did not assign player index");
    auto* joystick = SDL_GameControllerGetJoystick(pad);
    SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_LEFTX, 12345);
    SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_LEFTY, -23456);
    SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_RIGHTX, -32768);
    SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_RIGHTY, 32767);
    SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_TRIGGERLEFT, 32767);
    SDL_JoystickSetVirtualAxis(joystick, SDL_CONTROLLER_AXIS_TRIGGERRIGHT, -32768);
    SDL_JoystickSetVirtualButton(joystick, SDL_CONTROLLER_BUTTON_A, 1);
    SDL_PumpEvents();
    rex::input::X_INPUT_STATE state{};
    Require(input->GetState(user, &state) == 0, "state query failed");
    Require(uint16_t(state.gamepad.buttons) == rex::input::X_INPUT_GAMEPAD_A, "button mapping changed");
    Require(int16_t(state.gamepad.thumb_lx) == 12345 && int16_t(state.gamepad.thumb_ly) == 23455,
            "left stick mapping changed");
    Require(int16_t(state.gamepad.thumb_rx) == -32768 && int16_t(state.gamepad.thumb_ry) == -32768,
            "right stick endpoints changed");
    Require(state.gamepad.left_trigger == 255 && state.gamepad.right_trigger == 0,
            "trigger independence changed");
    auto packet = uint32_t(state.packet_number);
    rex::input::X_INPUT_KEYSTROKE key{};
    Require(input->GetKeystroke(user, 0, &key) == 0, "menu button down missing");
    Require(uint16_t(key.flags) == rex::input::X_INPUT_KEYSTROKE_KEYDOWN, "menu button down flags changed");
    rex::input::X_INPUT_VIBRATION vibration{};
    vibration.left_motor_speed = 12000;
    vibration.right_motor_speed = 34000;
    Require(input->SetState(user, &vibration) == 0 && rumble_calls == 1, "rumble forwarding changed");
    SDL_JoystickSetVirtualButton(joystick, SDL_CONTROLLER_BUTTON_A, 0);
    SDL_PumpEvents();
    Require(input->GetState(user, &state) == 0 && uint16_t(state.gamepad.buttons) == 0,
            "button release missing");
    Require(uint32_t(state.packet_number) == packet + 1, "release packet number unchanged");
    Require(input->GetKeystroke(user, 0, &key) == 0, "menu button up missing");
    Require(uint16_t(key.flags) == rex::input::X_INPUT_KEYSTROKE_KEYUP, "menu button up flags changed");
    SDL_JoystickDetachVirtual(device);
    SDL_PumpEvents();
    auto disconnected = input->GetState(user, &state);
    // The factory retains the SDK's idle player-one fallback.
    Require(user == 0 ? disconnected == 0 && uint16_t(state.gamepad.buttons) == 0
                      : disconnected == X_ERROR_DEVICE_NOT_CONNECTED,
            "disconnect/fallback not reported");
    owner.reset();
    std::cout << "SDL controller buttons, sticks, triggers, menu events, rumble and disconnect passed\n";
}
