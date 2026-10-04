// Read-only external profiling metadata. Compile against the exact installed SDK.
#include <cstddef>
#include <cstdio>
#include <rex/runtime.h>
#include <rex/graphics/graphics_system.h>
#include <rex/graphics/d3d12/command_processor.h>
#include <rex/version.h>

int main() {
    std::printf("{\"sdk\":\"%s\",\"runtime_graphics\":%zu,\"graphics_cp\":%zu,"
                "\"cp_frame\":%zu,\"cp_completed\":%zu,"
                "\"runtime_memory\":%zu,\"memory_virtual\":%zu}\n", REXGLUE_VERSION_STRING,
        offsetof(rex::Runtime, graphics_system_),
        offsetof(rex::graphics::GraphicsSystem, command_processor_),
        offsetof(rex::graphics::d3d12::D3D12CommandProcessor, frame_current_),
        offsetof(rex::graphics::d3d12::D3D12CommandProcessor, frame_completed_),
        offsetof(rex::Runtime, memory_),
        offsetof(rex::memory::Memory, virtual_membase_));
}
