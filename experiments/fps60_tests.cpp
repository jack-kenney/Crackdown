#include "generated/crackdown_pch.h"
#include <rex/cvar.h>
#include <windows.h>
#include <cstdio>
#include <cstdlib>

REX_EXTERN(sub_826BC3D0);
static uint64_t expected_argument;
static unsigned expected_cap, calls;
REX_EXTERN(__imp__sub_826BC3D0) {
    if (ctx.r3.u64 != expected_argument ||
        REX_LOAD_U32(0x82BAA330) != expected_cap) std::abort();
    ++calls;
}

int main() {
    auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x83000000ull, MEM_RESERVE, PAGE_READWRITE));
    if (!base || !VirtualAlloc(base + 0x82BA0000, 0x10000, MEM_COMMIT, PAGE_READWRITE)) std::abort();
    PPCContext ctx{};
    for (bool enabled : {false, true}) {
        rex::cvar::SetFlagByName("fps60_pacing_experiment", enabled ? "true" : "false");
        for (unsigned interval : {0u, 1u, 2u, 3u, 15u}) {
            // Preserve the callback's CPU/notification fields and high word.
            ctx.r3.u64 = 0x123456789ABC005Aull | (interval << 8);
            expected_argument = ctx.r3.u64;
            expected_cap = 32;
            REX_STORE_U32(0x82BAA330, expected_cap);
            if (enabled && interval == 2) {
                expected_argument = (expected_argument & ~uint64_t(0xF00)) | 0x100;
                expected_cap = 60;
            }
            sub_826BC3D0(ctx, base);
        }
    }
    if (calls != 10) std::abort();
    VirtualFree(base, 0, MEM_RELEASE);
    std::puts("FPS prototype defaults preserve pacing; experimental mode preserves callback fields");
}
