#define SDL_MAIN_HANDLED
#include "controller_input.h"
#include <SDL.h>
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <thread>

using rex::X_STATUS;

namespace {
struct State { std::mutex controllers; std::atomic<unsigned> events{0}, rumbles{0}; };

// Reproduce the SDK's lock order against the real SDL joystick lock. SDL's
// event watch takes the controller mutex while an event producer holds SDL.
class Driver final : public rex::input::InputDriver {
public:
    explicit Driver(State& state) : InputDriver(nullptr, 0), state_(state) {}
    rex::X_STATUS Setup() override { return X_STATUS_SUCCESS; }
    rex::X_RESULT GetCapabilities(uint32_t, uint32_t, rex::input::X_INPUT_CAPABILITIES*) override { return 123; }
    rex::X_RESULT GetState(uint32_t, rex::input::X_INPUT_STATE*) override { return is_active() ? 124 : 125; }
    rex::X_RESULT GetKeystroke(uint32_t, uint32_t, rex::input::X_INPUT_KEYSTROKE*) override { return 126; }
    rex::X_RESULT SetState(uint32_t, rex::input::X_INPUT_VIBRATION*) override {
        std::lock_guard guard(state_.controllers);
        SDL_LockJoysticks();
        ++state_.rumbles;
        SDL_UnlockJoysticks();
        return 127;
    }
private:
    State& state_;
};
void Require(bool condition) { if (!condition) std::abort(); }
}

int main(int argc, char**) {
    Require(SDL_Init(SDL_INIT_EVENTS | SDL_INIT_JOYSTICK) == 0);
    State state;
    SDL_AddEventWatch([](void* data, SDL_Event* event) {
        if (event->type == SDL_USEREVENT) {
            auto& state = *static_cast<State*>(data);
            std::lock_guard guard(state.controllers);
            ++state.events;
        }
        return 0;
    }, &state);
    std::unique_ptr<rex::input::InputDriver> driver = std::make_unique<Driver>(state);
    // The unsafe mode verifies that this reproducer detects the original bug
    // by timing out when run separately; it is never used by the normal test.
    if (argc == 1) driver = std::make_unique<OrderedControllerInput>(std::move(driver));
    Require(driver->Setup() == X_STATUS_SUCCESS);
    Require(driver->GetCapabilities(0, 0, nullptr) == 123);
    Require(driver->GetState(0, nullptr) == 124);
    driver->set_is_active_callback([] { return false; });
    Require(driver->GetState(0, nullptr) == 125);
    Require(driver->GetKeystroke(0, 0, nullptr) == 126);
    constexpr unsigned rounds = 50000;
    std::thread events([&] {
        for (unsigned i = 0; i < rounds; ++i) {
            SDL_LockJoysticks();
            SDL_Event event{};
            event.type = SDL_USEREVENT;
            Require(SDL_PushEvent(&event) >= 0);
            SDL_UnlockJoysticks();
            SDL_FlushEvent(SDL_USEREVENT);
        }
    });
    std::thread rumble([&] {
        for (unsigned i = 0; i < rounds; ++i)
            Require(driver->SetState(0, nullptr) == 127);
    });
    events.join();
    rumble.join();
    Require(state.events == rounds && state.rumbles == rounds);
    driver.reset();
    SDL_Quit();
    std::cout << "Concurrent controller events and rumble passed\n";
    return 0;
}
