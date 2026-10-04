#include "automation.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <chrono>
#include <cstring>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <thread>

#include <rex/input/input_system.h>
#include <rex/input/nop/nop_input_driver.h>
#include <rex/logging.h>
#include <rex/ui/presenter.h>
#include <rex/ui/virtual_key.h>

namespace {
struct Pad {
    uint16_t buttons;
    uint8_t lt, rt;
    int16_t lx, ly, rx, ry;
};
static_assert(sizeof(Pad) == 12);
struct SharedState {
    uint32_t magic, version;
    volatile LONG sequence;
    Pad pad;
    DWORD valid_until;
    volatile LONG capture_request, capture_done, capture_status, input_ack;
};
static_assert(sizeof(SharedState) == 44);
LONG Load(volatile LONG& value) { return InterlockedCompareExchange(&value, 0, 0); }
}

struct AutomationSession::Impl {
    HANDLE mapping = nullptr;
    SharedState* shared = nullptr;
    std::jthread capture;

    Impl() {
        auto name = L"Local\\CrackdownAutomation-" + std::to_wstring(GetCurrentProcessId());
        mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, 4096, name.c_str());
        if (!mapping) throw std::runtime_error("Cannot create automation mapping");
        shared = static_cast<SharedState*>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, 4096));
        if (!shared) { CloseHandle(mapping); throw std::runtime_error("Cannot map automation state"); }
        std::memset(shared, 0, sizeof(*shared));
        shared->magic = 0x43444131;
        shared->version = 1;
        REXLOG_INFO("Automation controller enabled: Local\\CrackdownAutomation-{}", GetCurrentProcessId());
    }
    ~Impl() {
        Stop();
        UnmapViewOfFile(shared);
        CloseHandle(mapping);
    }
    void Stop() {
        capture.request_stop();
        if (capture.joinable()) capture.join();
    }
    bool Read(Pad& pad, LONG& sequence) {
        for (int attempt = 0; attempt < 3; ++attempt) {
            auto before = Load(shared->sequence);
            if (before & 1) continue;
            Pad candidate = shared->pad;
            DWORD until = shared->valid_until;
            MemoryBarrier();
            if (before != Load(shared->sequence)) continue;
            sequence = before;
            // A bounded lease releases all controls if the sender exits or stalls.
            pad = static_cast<LONG>(until - GetTickCount()) > 0 ? candidate : Pad{};
            InterlockedExchange(&shared->input_ack, before);
            return true;
        }
        pad = {};
        return false;
    }
    class Driver : public rex::input::InputDriver {
    public:
        explicit Driver(std::shared_ptr<Impl> impl) : InputDriver(nullptr, 0), impl_(std::move(impl)), idle_(nullptr, 0) {}
        rex::X_STATUS Setup() override { return idle_.Setup(); }
        void EnumerateDevices(std::vector<rex::input::DeviceInfo>& out) override { idle_.EnumerateDevices(out); }
        rex::X_RESULT GetDeviceCapabilities(rex::input::DeviceId user, uint32_t flags, rex::input::X_INPUT_CAPABILITIES* caps) override {
            return idle_.GetDeviceCapabilities(user, flags, caps);
        }
        rex::X_RESULT SetDeviceVibration(rex::input::DeviceId user, rex::input::X_INPUT_VIBRATION* vibration) override {
            return idle_.SetDeviceVibration(user, vibration);
        }
        rex::X_RESULT GetDeviceKeystroke(rex::input::DeviceId user, uint32_t flags, rex::input::X_INPUT_KEYSTROKE* key) override {
            using rex::X_RESULT;
            using rex::ui::VirtualKey;
            if (user != static_cast<rex::input::DeviceId>(0x4E4F5000)) return X_ERROR_DEVICE_NOT_CONNECTED;
            if (!key) return X_ERROR_BAD_ARGUMENTS;
            std::lock_guard lock(key_mutex_);
            Pad pad{};
            LONG sequence = 0;
            impl_->Read(pad, sequence);
            static constexpr VirtualKey keys[] = {
                VirtualKey::kXInputPadDpadUp, VirtualKey::kXInputPadDpadDown,
                VirtualKey::kXInputPadDpadLeft, VirtualKey::kXInputPadDpadRight,
                VirtualKey::kXInputPadStart, VirtualKey::kXInputPadBack,
                VirtualKey::kXInputPadLThumbPress, VirtualKey::kXInputPadRThumbPress,
                VirtualKey::kXInputPadLShoulder, VirtualKey::kXInputPadRShoulder,
                VirtualKey::kNone, VirtualKey::kNone,
                VirtualKey::kXInputPadA, VirtualKey::kXInputPadB,
                VirtualKey::kXInputPadX, VirtualKey::kXInputPadY
            };
            for (int bit = 0; bit < 16; ++bit) {
                uint16_t mask = uint16_t(1u << bit);
                if (keys[bit] == VirtualKey::kNone || !((pad.buttons ^ keys_down_) & mask)) continue;
                *key = {};
                key->virtual_key = uint16_t(keys[bit]);
                key->flags = (pad.buttons & mask) ? rex::input::X_INPUT_KEYSTROKE_KEYDOWN : rex::input::X_INPUT_KEYSTROKE_KEYUP;
                keys_down_ ^= mask;
                return X_ERROR_SUCCESS;
            }
            return X_ERROR_EMPTY;
        }
        rex::X_RESULT GetDeviceState(rex::input::DeviceId user, rex::input::X_INPUT_STATE* state) override {
            using rex::X_RESULT;
            if (user != static_cast<rex::input::DeviceId>(0x4E4F5000)) return X_ERROR_DEVICE_NOT_CONNECTED;
            // XamInputGetState also uses a null state as a connection query.
            if (!state) return X_ERROR_SUCCESS;
            std::lock_guard lock(key_mutex_);
            Pad pad{};
            LONG sequence = 0;
            impl_->Read(pad, sequence);
            std::memset(state, 0, sizeof(*state));
            if (std::memcmp(&pad, &last_pad_, sizeof(Pad))) {
                last_pad_ = pad;
                ++packet_;
            }
            state->packet_number = packet_;
            state->gamepad.buttons = pad.buttons;
            state->gamepad.left_trigger = pad.lt;
            state->gamepad.right_trigger = pad.rt;
            state->gamepad.thumb_lx = pad.lx;
            state->gamepad.thumb_ly = pad.ly;
            state->gamepad.thumb_rx = pad.rx;
            state->gamepad.thumb_ry = pad.ry;
            return X_ERROR_SUCCESS;
        }
    private:
        std::shared_ptr<Impl> impl_;
        rex::input::nop::NopInputDriver idle_;
        std::mutex key_mutex_;
        uint16_t keys_down_ = 0;
        Pad last_pad_{};
        uint32_t packet_ = 0;
    };
};

AutomationSession::AutomationSession() : impl_(std::make_shared<Impl>()) {}
AutomationSession::~AutomationSession() { StopCapture(); }

std::unique_ptr<rex::system::IInputSystem> AutomationSession::CreateInputSystem() {
    auto input = std::make_unique<rex::input::InputSystem>(nullptr);
    input->AddDriver(std::make_unique<Impl::Driver>(impl_));
    input->SetDeviceAssignment(std::make_unique<rex::input::SlotAssignment>());
    // Automation owns player one. Normal launches retain the SDK's SDL factory.
    return input;
}

void AutomationSession::StartCapture(rex::ui::Presenter* presenter, const std::filesystem::path& directory) {
    std::filesystem::create_directories(directory);
    auto* impl = impl_.get();
    impl->capture = std::jthread([impl, presenter, directory](std::stop_token stop) {
        while (!stop.stop_requested()) {
            auto request = Load(impl->shared->capture_request);
            if (request != Load(impl->shared->capture_done)) {
                bool success = false;
                try {
                    rex::ui::RawImage image;
                    if (presenter && presenter->CaptureGuestOutput(image) && image.width && image.height) {
                        auto path = directory / ("frame-" + std::to_string(request) + ".ppm");
                        std::ofstream file(path, std::ios::binary);
                        file << "P6\n" << image.width << ' ' << image.height << "\n255\n";
                        for (uint32_t y = 0; y < image.height; ++y)
                            for (uint32_t x = 0; x < image.width; ++x)
                                file.write(reinterpret_cast<const char*>(image.data.data() + y * image.stride + x * 4), 3);
                        success = file.good();
                        if (success) REXLOG_INFO("Automation captured {}", path.string());
                    }
                } catch (const std::exception& e) { REXLOG_ERROR("Automation capture failed: {}", e.what()); }
                InterlockedExchange(&impl->shared->capture_status, success ? 1 : -1);
                InterlockedExchange(&impl->shared->capture_done, request);
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    });
}
void AutomationSession::StopCapture() { if (impl_) impl_->Stop(); }
#endif
