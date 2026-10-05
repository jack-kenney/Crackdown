cmake_minimum_required(VERSION 3.25)
project(crackdown_renderer LANGUAGES CXX)

# Build just the GPU plugin against the released runtime. No SDK submodules,
# runtime rebuild, or replacement of the installed SDK is needed.
find_package(rexglue 0.10.0 EXACT REQUIRED CONFIG)
if(NOT WIN32 OR NOT CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    message(FATAL_ERROR "This renderer experiment requires Windows and Clang")
endif()
if(NOT CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR "Use Release to match the installed runtime ABI")
endif()
set(REX_SOURCE "" CACHE PATH "ReXGlue 0.10.0 source checkout")
set(COMMAND_PROCESSOR_SOURCE "${REX_SOURCE}/src/graphics/command_processor.cpp"
    CACHE FILEPATH "Command processor source, optionally the unpatched baseline")
if(NOT EXISTS "${REX_SOURCE}/src/graphics/plugin_main.cpp")
    message(FATAL_ERROR "Set REX_SOURCE to the ReXGlue 0.10.0 source checkout")
endif()

# Matches src/graphics/CMakeLists.txt at f5337cdc (ReXGlue 0.10.0, D3D12).
set(gpu_sources
    plugin_main.cpp flags.cpp register_file.cpp registers.cpp
    pipeline/shader/shader.cpp format/ucode.cpp sampler_info.cpp
    graphics_system.cpp util/draw_extent_estimator.cpp util/draw.cpp
    packet_disassembler.cpp primitive_processor.cpp
    pipeline/render_target/cache.cpp shared_memory.cpp pipeline/texture/cache.cpp
    pipeline/shader/interpreter.cpp pipeline/shader/translator.cpp
    pipeline/shader/translator_disasm.cpp
    pipeline/shader/dxbc.cpp pipeline/shader/dxbc_translator.cpp
    pipeline/shader/dxbc_translator_alu.cpp pipeline/shader/dxbc_translator_fetch.cpp
    pipeline/shader/dxbc_translator_memexport.cpp pipeline/shader/dxbc_translator_om.cpp
    d3d12/graphics_system.cpp d3d12/command_processor.cpp
    d3d12/primitive_processor.cpp d3d12/render_target_cache.cpp d3d12/shader.cpp
    d3d12/shared_memory.cpp d3d12/texture_cache.cpp d3d12/deferred_command_list.cpp
    d3d12/pipeline_cache.cpp)
option(RENDERER_GPU_TIMING "Build the optional GPU timestamp profiler" OFF)
if(RENDERER_GPU_TIMING)
    list(APPEND gpu_sources d3d12/gpu_timing.cpp)
endif()
list(TRANSFORM gpu_sources PREPEND "${REX_SOURCE}/src/graphics/")
add_library(crackdown_renderer SHARED ${gpu_sources}
    "${COMMAND_PROCESSOR_SOURCE}" "${REX_SOURCE}/thirdparty/dxbc/DXBCChecksum.cpp")
set_target_properties(crackdown_renderer PROPERTIES
    OUTPUT_NAME rexgpu-xenos CXX_STANDARD 23 CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
target_compile_definitions(crackdown_renderer PRIVATE
    REX_PLATFORM_WINDOWS=1 REXGLUE_BUILD_CONFIG="Release")
target_compile_options(crackdown_renderer PRIVATE
    -march=x86-64-v2 -O3 -fno-strict-aliasing -ffp-model=strict -fno-char8_t
    -gcodeview -g -Wno-unused-variable)
target_include_directories(crackdown_renderer PRIVATE "${REX_SOURCE}/include" "${REX_SOURCE}")
target_link_libraries(crackdown_renderer PRIVATE
    rex::runtime rex::xxhash rex::renderdoc d3d12 dxgi dxguid)
target_link_options(crackdown_renderer PRIVATE "LINKER:/debug,/opt:ref,/opt:icf,/map")
include("${REX_SOURCE}/cmake/rex_pch.cmake")
rex_target_pch(crackdown_renderer
    <mutex> <unordered_map> <rex/math.h> <rex/memory.h> <rex/thread/mutex.h>
    <rex/system/xmemory.h> <rex/graphics/xenos.h> <rex/graphics/registers.h>
    <rex/graphics/format/ucode.h> <rex/graphics/pipeline/shader/shader.h>)
set_source_files_properties("${REX_SOURCE}/thirdparty/dxbc/DXBCChecksum.cpp"
    PROPERTIES SKIP_PRECOMPILE_HEADERS ON)

# Regenerate private offsets from the same headers as the plugin. Metadata is
# paired with the resulting DLL hashes before read-only frame sampling.
add_executable(renderer_offsets ../benchmark_offsets.cpp)
set_target_properties(renderer_offsets PROPERTIES CXX_STANDARD 23 CXX_STANDARD_REQUIRED ON)
target_compile_options(renderer_offsets PRIVATE -fno-access-control -Wno-invalid-offsetof)
target_include_directories(renderer_offsets PRIVATE "${REX_SOURCE}/include")
target_link_libraries(renderer_offsets PRIVATE rex::runtime rex::renderdoc)
