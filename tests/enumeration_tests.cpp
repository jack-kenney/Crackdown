#include "crackdown_pch.h"
#include <array>
#include <cstdlib>
#include <cstdio>

REX_EXTERN(__imp__XamEnumerate);
static uint32_t result;
static bool async;
namespace rex::kernel::xam {
uint32_t xeXamEnumerate(uint32_t, uint32_t, mapped_void, uint32_t,
    uint32_t* count, uint32_t overlapped) {
    async = overlapped != 0 && count == nullptr;
    if (result == 0 && count) *count = 3;
    return result; // Invalid-handle path deliberately leaves count untouched.
}
}
int main() {
    alignas(64) std::array<uint8_t, 1024> memory{};
    auto* base = memory.data();
    for (uint32_t status : {0u, 6u, 87u, 18u}) {
        PPCContext ctx{};
        ctx.r5.u32 = 128; ctx.r6.u32 = 208; ctx.r7.u32 = 64;
        REX_STORE_U32(64, 0xDEADBEEF);
        result = status;
        __imp__XamEnumerate(ctx, base);
        if (ctx.r3.u32 != status || REX_LOAD_U32(64) != (status == 0 ? 3 : 0)) std::abort();
    }
    PPCContext ctx{};
    ctx.r7.u32 = 64; ctx.r8.u32 = 256;
    REX_STORE_U32(64, 0xDEADBEEF);
    result = 997;
    __imp__XamEnumerate(ctx, base);
    if (!async || ctx.r3.u32 != 997 || REX_LOAD_U32(64) != 0xDEADBEEF) std::abort();
    std::puts("Enumeration count is deterministic on success/error; async output preserved");
}
