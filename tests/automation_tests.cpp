#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "automation.h"

#include <cstdio>
#include <cstring>
#include <rex/input/input_system.h>
#include <rex/ui/virtual_key.h>

using rex::X_RESULT;
static int failures = 0;
#define CHECK(expression) do { if (!(expression)) { \
    std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expression); ++failures; \
} } while (false)

int main() {
    AutomationSession session;
    auto system = session.CreateInputSystem();
    auto* input = static_cast<rex::input::InputSystem*>(system.get());
    auto name = L"Local\\CrackdownAutomation-" + std::to_wstring(GetCurrentProcessId());
    HANDLE mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name.c_str());
    CHECK(mapping != nullptr);
    auto* bytes = static_cast<unsigned char*>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, 4096));
    CHECK(bytes != nullptr);
    if (!bytes) return 1;
    auto word = [&](int offset) -> volatile LONG& { return *reinterpret_cast<volatile LONG*>(bytes + offset); };
    rex::input::X_INPUT_STATE state{};
    rex::input::X_INPUT_KEYSTROKE key{};
    CHECK(input->GetState(0, &state) == X_ERROR_SUCCESS);
    CHECK(state.gamepad.buttons == 0);
    CHECK(input->GetState(1, &state) == X_ERROR_DEVICE_NOT_CONNECTED);
    CHECK(input->GetState(0, nullptr) == X_ERROR_SUCCESS);

    // Write the public little-endian wire format, then check the SDK's
    // big-endian state and menu key events rather than only an internal parser.
    InterlockedExchange(&word(8), 1);
    const uint16_t buttons = 0x1010; // A + Start
    const int16_t lx = -24000;
    std::memcpy(bytes + 12, &buttons, 2);
    bytes[14] = 128;
    std::memcpy(bytes + 16, &lx, 2);
    DWORD lease = GetTickCount() + 1000;
    std::memcpy(bytes + 24, &lease, 4);
    CHECK(input->GetState(0, &state) == X_ERROR_SUCCESS && state.gamepad.buttons == 0);
    InterlockedExchange(&word(8), 2);
    CHECK(input->GetState(0, &state) == X_ERROR_SUCCESS);
    CHECK(state.gamepad.buttons == buttons && state.gamepad.left_trigger == 128 && state.gamepad.thumb_lx == lx);
    CHECK(reinterpret_cast<unsigned char*>(&state.gamepad.buttons)[0] == 0x10);
    CHECK(word(40) == 2);
    uint32_t pressed_packet = state.packet_number;
    CHECK(input->GetKeystroke(0, 0, &key) == X_ERROR_SUCCESS);
    CHECK(key.virtual_key == uint16_t(rex::ui::VirtualKey::kXInputPadStart));
    CHECK(key.flags == rex::input::X_INPUT_KEYSTROKE_KEYDOWN);
    CHECK(input->GetKeystroke(0, 0, &key) == X_ERROR_SUCCESS);
    CHECK(key.virtual_key == uint16_t(rex::ui::VirtualKey::kXInputPadA));
    CHECK(input->GetKeystroke(0, 0, &key) == X_ERROR_EMPTY);

    // Sender disappearance releases controls and changes packet number even
    // without a new sequence, so games that cache packets see the release.
    lease = GetTickCount() - 1;
    std::memcpy(bytes + 24, &lease, 4);
    CHECK(input->GetState(0, &state) == X_ERROR_SUCCESS && state.gamepad.buttons == 0);
    CHECK(state.gamepad.thumb_lx == 0 && state.gamepad.left_trigger == 0);
    CHECK(state.packet_number != pressed_packet);
    CHECK(input->GetKeystroke(0, 0, &key) == X_ERROR_SUCCESS);
    CHECK(key.flags == rex::input::X_INPUT_KEYSTROKE_KEYUP);
    CHECK(input->GetKeystroke(0, 0, &key) == X_ERROR_SUCCESS);
    CHECK(input->GetKeystroke(0, 0, &key) == X_ERROR_EMPTY);
    UnmapViewOfFile(bytes);
    CloseHandle(mapping);
    std::printf("Automation tests: %d failures\n", failures);
    return failures ? 1 : 0;
}
