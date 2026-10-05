#include "generated/crackdown_pch.h"
#include <rex/cvar.h>
#include <windows.h>
#include <array>
#include <bit>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>

namespace crackdown::experiments { unsigned NativeFrameRate() { return 240; } }

REX_EXTERN(sub_822B97B0);
REX_EXTERN(sub_822AB708);
REX_EXTERN(sub_822A9E78);
REX_EXTERN(sub_829BD048);
#define DECLARE_STAGE(address) REX_EXTERN(sub_##address);
DECLARE_STAGE(822AC7C8)
DECLARE_STAGE(822AC9D8)
DECLARE_STAGE(822ACC08)
DECLARE_STAGE(822B4100)
DECLARE_STAGE(822B4D40)
DECLARE_STAGE(822ACF68)
DECLARE_STAGE(822AD4F0)
DECLARE_STAGE(822B1B60)
DECLARE_STAGE(822AF9C0)
DECLARE_STAGE(822B02B0)
DECLARE_STAGE(822B1058)
DECLARE_STAGE(822B15C8)
DECLARE_STAGE(822B0C28)
#undef DECLARE_STAGE
namespace {
constexpr uint32_t agent = 0x100000, world = 0x102000;
constexpr std::array<uint32_t, 3> pages{0x100000, 0x82D90000, 0x82DE0000};
unsigned calls = 0, checks = 0;
bool disabled = false;
void Check(bool value, const char* message) {
    ++checks;
    if (!value) { std::fprintf(stderr, "Physics trace test: %s\n", message); std::abort(); }
}
void StoreFloat(uint32_t address, float value, uint8_t* base) {
    REX_STORE_U32(address, std::bit_cast<uint32_t>(value));
}
void Original(PPCContext& ctx, uint8_t* base, unsigned kind) {
    ++calls;
    Check(ctx.f1.f64 == 0.011 && ctx.lr == 0x12345678, "original receives the exact input delta/caller");
    if (!disabled) {
        if (kind == 3) StoreFloat(world + 16, 33.011f, base);
        else {
            StoreFloat(agent + 320, 101.0f, base);
            StoreFloat(agent + 1296, 42.0f, base);
            StoreFloat(agent + 1344 + 352, 43.0f, base);
        }
    }
    // Deliberately overwrite the argument and output registers. The observer
    // must use the saved actor/world address, and preserve every returned bit.
    ctx.r3.u64 = 0xaabbccdd11223344ull;
    ctx.r5.u64 = 0xfedcba9876543210ull;
    ctx.f1.f64 = 123.125;
    ctx.lr = 0xabcdef01;
}
}
REX_EXTERN(__imp__sub_822B97B0) { Original(ctx, base, 0); }
REX_EXTERN(__imp__sub_822AB708) { Original(ctx, base, 1); }
REX_EXTERN(__imp__sub_822A9E78) { Original(ctx, base, 2); }
REX_EXTERN(__imp__sub_829BD048) { Original(ctx, base, 3); }
#define ORIGINAL_STAGE(address) REX_EXTERN(__imp__sub_##address) { Original(ctx, base, 4); }
ORIGINAL_STAGE(822AC7C8)
ORIGINAL_STAGE(822AC9D8)
ORIGINAL_STAGE(822ACC08)
ORIGINAL_STAGE(822B4100)
ORIGINAL_STAGE(822B4D40)
ORIGINAL_STAGE(822ACF68)
ORIGINAL_STAGE(822AD4F0)
ORIGINAL_STAGE(822B1B60)
ORIGINAL_STAGE(822AF9C0)
ORIGINAL_STAGE(822B02B0)
ORIGINAL_STAGE(822B1058)
ORIGINAL_STAGE(822B15C8)
ORIGINAL_STAGE(822B0C28)
#undef ORIGINAL_STAGE

int main(int argc, char** argv) {
    disabled = argc < 2 || std::string(argv[1]) == "--disabled";
    const std::string path = disabled ? "" : argv[1];
    Check(rex::cvar::SetFlagByName("physics_trace_path", path), "select trace mode before first call");
    uint8_t* base = nullptr;
    if (!disabled) {
        base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x83000000ull, MEM_RESERVE, PAGE_READWRITE));
        Check(base != nullptr, "reserve guest address space");
        for (const auto page : pages) Check(VirtualAlloc(base + page, 0x10000, MEM_COMMIT, PAGE_READWRITE) != nullptr, "commit fixture page");
        REX_STORE_U32(0x82DE25C0, 0x104000);
        REX_STORE_U32(0x82DE3F3C, 0x103000);
        REX_STORE_U32(0x103000 + 292, agent + 32);
        REX_STORE_U32(0x82D99128, 11);
        REX_STORE_U32(0x82D9912C, 1000);
        StoreFloat(agent + 320, 100.0f, base);
        REX_STORE_U32(agent + 1344 + 660, 0x106000);
        REX_STORE_U32(0x106000 + 48, 0x107000);
        REX_STORE_U32(0x107000 + 28, 0x108000);
        StoreFloat(0x108000 + 48, 107.25f, base);
        StoreFloat(world + 16, 33.0f, base);
    }
    using Function = void (*)(PPCContext&, uint8_t*);
#define STAGE_FUNCTIONS(prefix) prefix##822AC7C8, prefix##822AC9D8, prefix##822ACC08, \
    prefix##822B4100, prefix##822B4D40, prefix##822ACF68, prefix##822AD4F0, \
    prefix##822B1B60, prefix##822AF9C0, prefix##822B02B0, prefix##822B1058, \
    prefix##822B15C8, prefix##822B0C28
    const std::array<Function, 17> originals{__imp__sub_822B97B0, __imp__sub_822AB708,
        __imp__sub_822A9E78, __imp__sub_829BD048, STAGE_FUNCTIONS(__imp__sub_)};
    const std::array<Function, 17> hooks{sub_822B97B0, sub_822AB708, sub_822A9E78,
        sub_829BD048, STAGE_FUNCTIONS(sub_)};
#undef STAGE_FUNCTIONS
    for (unsigned kind = 0; kind < hooks.size(); ++kind) {
        PPCContext incoming{};
        incoming.r3.u32 = kind == 3 ? 0x105000 : kind == 0 ? agent : agent + 1344;
        incoming.r4.u32 = world;
        incoming.r27.u64 = 0x1122334455667788ull;
        incoming.f1.f64 = 0.011;
        incoming.f2.f64 = 0.011;
        incoming.lr = 0x12345678;
        std::array<std::array<uint8_t, 0x10000>, 3> before{}, expected_memory{};
        if (base) for (unsigned i = 0; i < pages.size(); ++i) std::memcpy(before[i].data(), base + pages[i], 0x10000);
        auto expected = incoming;
        originals[kind](expected, base);
        if (base) for (unsigned i = 0; i < pages.size(); ++i) {
            std::memcpy(expected_memory[i].data(), base + pages[i], 0x10000);
            std::memcpy(base + pages[i], before[i].data(), 0x10000);
        }
        auto actual = incoming;
        const auto count = calls;
        hooks[kind](actual, base);
        Check(calls == count + 1, "hook forwards exactly once");
        Check(std::memcmp(&actual, &expected, sizeof(actual)) == 0, "complete returned PPC context preserved");
        if (base) for (unsigned i = 0; i < pages.size(); ++i)
            Check(std::memcmp(base + pages[i], expected_memory[i].data(), 0x10000) == 0, "observer adds no guest memory writes");
    }
    if (!disabled && path.find("missing-parent") == std::string::npos) {
        std::this_thread::sleep_for(std::chrono::milliseconds(350));
        std::ifstream file(path);
        const std::string csv{std::istreambuf_iterator<char>(file), {}};
        for (const auto* kind : {"agent_update", "controller_proxy", "controller_local", "havok_step",
                "prepare_motion", "prepare_support", "prepare_contact", "step_up", "step_down",
                "local_solve", "proxy_solve", "update_contact", "update_support", "update_surface",
                "update_velocity", "update_vertical", "update_water"})
            Check(csv.find(kind) != std::string::npos, "background writer emitted each hooked path");
        Check(csv.find(",12345678,1,11,1000,") != std::string::npos, "writer retained caller/player/clock observations");
        Check(csv.find("107.25") != std::string::npos, "writer follows the phantom position chain");
    }
    if (base) VirtualFree(base, 0, MEM_RELEASE);
    std::printf("Physics trace forwarding checks passed (%u checks)\n", checks);
}
