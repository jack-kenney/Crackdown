#pragma once

#include <filesystem>
#include <memory>

namespace rex::system { class IInputSystem; }
namespace rex::ui { class Presenter; }

// Explicitly enabled local controller/frame capture interface. No network listener.
class AutomationSession {
public:
    AutomationSession();
    ~AutomationSession();
    std::unique_ptr<rex::system::IInputSystem> CreateInputSystem();
    void StartCapture(rex::ui::Presenter* presenter, const std::filesystem::path& directory);
    void StopCapture();
private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
