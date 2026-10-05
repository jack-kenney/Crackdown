#include "generated/crackdown_pch.h"
#include "experiments/character_step_down.h"
#include <rex/cvar.h>
#include <windows.h>
#include <array>
#include <bit>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

namespace {
constexpr uint32_t output = 0x100000;
unsigned native_rate = 240, calls = 0, checks = 0;
bool success = true;
float original_vertical = -20;
PPCContext expected_input{};
void Check(bool value, const char* message) {
    ++checks;
    if (!value) { std::fprintf(stderr, "Character step-down: %s\n", message); std::abort(); }
}
void Store(uint32_t address, float value, uint8_t* base) {
    REX_STORE_U32(address, std::bit_cast<uint32_t>(value));
}
}
namespace crackdown::experiments { unsigned NativeFrameRate() { return native_rate; } }
REX_EXTERN(__imp__sub_822B4D40) {
    ++calls;
    Check(std::memcmp(&ctx, &expected_input, sizeof(ctx)) == 0, "original input context is unchanged");
    if (base) { Store(output, 3, base); Store(output + 4, original_vertical, base); Store(output + 8, 4, base); }
    ctx.r3.u64 = success ? 0x1234000000000001ull : 0;
    ctx.r5.u64 = 0xdeadbeef;
    ctx.f1.f64 = 123.5;
    ctx.lr = 0x76543210;
}

int main() {
    using namespace crackdown::experiments;
    for (int hz : {30, 60, 120, 144, 240}) {
        const double dt = 1.0 / hz;
        double remaining = 1;
        for (int i = 0; i < hz / 2; ++i) remaining *= 1 - 0.4 * StepDownCorrectionScale(dt);
        Check(std::abs(remaining - std::pow(0.6, 15)) < 1e-12,
            "half-second correction response is invariant across supported frame rates");
    }
    double remaining = 1, elapsed = 0;
    for (double dt : {.004, .008, .011, .020, .006, .015}) {
        elapsed += dt;
        remaining *= 1 - .4 * StepDownCorrectionScale(dt);
    }
    Check(std::abs(remaining - std::pow(.6, elapsed * 30)) < 1e-12,
        "variable short updates preserve the same elapsed-time response");
    for (double dt : {0.0, -1.0, 1.0 / 30, .05, std::numeric_limits<double>::infinity(),
                     std::numeric_limits<double>::quiet_NaN()})
        Check(StepDownCorrectionScale(dt) == 1, "reference, long and invalid deltas pass through");

    auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x110000, MEM_RESERVE, PAGE_READWRITE));
    Check(base && VirtualAlloc(base + output, 0x1000, MEM_COMMIT, PAGE_READWRITE), "allocate output fixture");
    auto run = [&](bool enabled, unsigned hz, uint32_t caller, double dt, bool returned_success,
                   float vertical, uint32_t output_argument, bool should_change, uint8_t* memory) {
        Check(rex::cvar::SetFlagByName("normalize_character_step_down", enabled ? "true" : "false"), "set test mode");
        native_rate = hz; success = returned_success; original_vertical = vertical;
        PPCContext incoming{};
        incoming.r3.u32 = 0x200000;
        incoming.r5.u32 = output_argument;
        incoming.lr = caller;
        incoming.f1.f64 = dt;
        incoming.r27.u64 = 0x0123456789abcdefull;
        expected_input = incoming;
        std::array<uint8_t, 0x1000> before{}, expected_memory{};
        if (memory) std::memcpy(before.data(), memory + output, before.size());
        auto expected = incoming;
        __imp__sub_822B4D40(expected, memory);
        if (memory) {
            if (should_change) Store(output + 4, float(vertical * StepDownCorrectionScale(dt)), memory);
            std::memcpy(expected_memory.data(), memory + output, expected_memory.size());
            std::memcpy(memory + output, before.data(), before.size());
        }
        auto actual = incoming;
        const auto count = calls;
        StepDownWithCorrection(actual, memory);
        Check(calls == count + 1, "original dispatch executes exactly once");
        Check(std::memcmp(&actual, &expected, sizeof(actual)) == 0, "all returned context bits are preserved");
        if (memory) Check(std::memcmp(memory + output, expected_memory.data(), expected_memory.size()) == 0,
            "only eligible downward output changes; horizontal output and surrounding memory preserved");
    };
    run(false, 240, 0x822AA884, .008, true, -20, output, false, nullptr);
    run(false, 240, 0x822AA884, .008, true, -20, output, false, base);
    run(true, 0, 0x822AA884, .008, true, -20, output, false, base);
    run(true, 240, 0x12345678, .008, true, -20, output, false, base);
    run(true, 240, 0x822AA884, .008, false, -20, output, false, base);
    run(true, 240, 0x822AA884, .008, true, 20, output, false, base);
    run(true, 240, 0x822AA884, .008, true, -20, 0, false, base);
    run(true, 240, 0x822AA884, .05, true, -20, output, false, base);
    for (unsigned hz : {60u, 120u, 144u, 240u})
        run(true, hz, 0x822AA884, 1.0 / hz, true, -20, output, true, base);
    run(true, 240, 0x822AA884, .008, true, std::numeric_limits<float>::quiet_NaN(), output, false, base);
    VirtualFree(base, 0, MEM_RELEASE);
    std::printf("Character step-down checks passed (%u checks)\n", checks);
}
