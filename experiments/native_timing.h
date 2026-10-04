#pragma once
#include <cstdint>
struct PPCContext;
namespace crackdown::experiments {
unsigned NativeFrameRate();
void PrepareNativeUpdate(PPCContext& ctx, uint8_t* base);
}
