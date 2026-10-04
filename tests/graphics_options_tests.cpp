#include "crackdown_pch.h"
#include "graphics_options.h"
#include <rex/cvar.h>
#include <windows.h>
#include <cstdio>
#include <cstdlib>

REXCVAR_DEFINE_BOOL(show_perfgraph, false, "Test", "Test overlay request");
REX_EXTERN(sub_826A7AD0);
static unsigned calls;
static bool expected_ready, expected_graph;
REX_EXTERN(__imp__sub_826A7AD0) {
    if (REX_LOAD_U32(0x82DE4F14) != unsigned(expected_ready) ||
        REX_LOAD_U8(0x82DE4D1A) != unsigned(expected_graph)) std::abort();
    ++calls;
}
int main() {
    auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x83000000ull, MEM_RESERVE, PAGE_READWRITE));
    if (!base || !VirtualAlloc(base + 0x82D10000, 0x10000, MEM_COMMIT, PAGE_READWRITE) ||
        !VirtualAlloc(base + 0x82DE0000, 0x10000, MEM_COMMIT, PAGE_READWRITE)) std::abort();
    PPCContext ctx{};
    for (bool graph : {false, true}) {
        rex::cvar::SetFlagByName("show_fps", graph ? "false" : "true");
        rex::cvar::SetFlagByName("show_perfgraph", graph ? "true" : "false");
        for (bool ready : {false, true, false}) {
            REX_STORE_U32(0x82D1AEDC, ready ? 0x40000000 : 0);
            expected_ready = ready; expected_graph = ready && graph;
            sub_826A7AD0(ctx, base);
        }
    }
    if (calls != 6) std::abort();
    VirtualFree(base, 0, MEM_RELEASE);
    std::puts("FPS/graph wait for guest shader initialization and survive teardown");
}
