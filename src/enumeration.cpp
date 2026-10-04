#include "crackdown_pch.h"

// The SDK's exported worker returns before filling the count on an invalid
// handle. Its 0.10 wrapper copies an uninitialized local into guest memory.
// TU0's optional XLive enumeration then walks beyond its ten-entry stack buffer.
namespace rex::kernel::xam {
uint32_t xeXamEnumerate(uint32_t handle, uint32_t flags, mapped_void buffer,
    uint32_t size, uint32_t* count, uint32_t overlapped);
}

REX_EXTERN(__imp__XamEnumerate) {
    const auto count_address = ctx.r7.u32;
    const auto overlapped = ctx.r8.u32;
    const auto buffer_address = ctx.r5.u32;
    uint32_t count = 0;
    const auto result = rex::kernel::xam::xeXamEnumerate(
        ctx.r3.u32, ctx.r4.u32,
        mapped_void(buffer_address ? REX_RAW_ADDR(buffer_address) : nullptr, buffer_address),
        ctx.r6.u32, overlapped ? nullptr : &count, overlapped);
    if (!overlapped && count_address) {
        REX_STORE_U32(count_address, result == 0 ? count : 0);
    }
    ctx.r3.u64 = result;
}
