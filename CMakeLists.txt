cmake_minimum_required(VERSION 3.25)
project(crackdown LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(REXSDK_DIR "" CACHE PATH "Path to rexglue-sdk source tree")
if(REXSDK_DIR)
    add_subdirectory("${REXSDK_DIR}" rexglue-sdk)
else()
    find_package(rexglue 0.10.0 EXACT QUIET CONFIG)
    if(NOT rexglue_FOUND)
        message(FATAL_ERROR
            "ReXGlue SDK not found. Either:\n"
            "  - Set REXSDK_DIR to the rexglue-sdk source tree (e.g. thirdparty/rexglue-sdk)\n"
            "  - Install the SDK package and ensure it is on CMAKE_PREFIX_PATH")
    endif()
endif()

find_package(Python3 3.8 REQUIRED COMPONENTS Interpreter)
set(CRACKDOWN_PACK_FIX "${CMAKE_CURRENT_SOURCE_DIR}/tools/fix_vector_packing.py")
add_custom_target(crackdown_pack_fix
    COMMAND "${Python3_EXECUTABLE}" "${CRACKDOWN_PACK_FIX}" "${CMAKE_CURRENT_SOURCE_DIR}/generated"
    COMMENT "Checking generated FLOAT16_4 input snapshots"
    VERBATIM
)

# Sources
set(CRACKDOWN_SOURCES
    src/main.cpp
    src/cache.cpp
    src/audio_barrier.cpp
    src/audio_spatial.cpp
    src/controller_input.cpp
    src/startup_movies.cpp
    src/graphics_options.cpp
    src/enumeration.cpp
)

# Include generated code if codegen has been run
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/generated/sources.cmake")
    include(generated/sources.cmake)
    list(APPEND CRACKDOWN_SOURCES ${GENERATED_SOURCES})
endif()

if(WIN32)
    list(APPEND CRACKDOWN_SOURCES src/automation.cpp src/timer_resolution.cpp)
    add_executable(crackdown WIN32 ${CRACKDOWN_SOURCES})
    # Keep function names available for release crash dumps without recompiling
    # the large generated translation units with debug information.
    target_link_options(crackdown PRIVATE "LINKER:/debug,/map,/opt:ref,/opt:icf")
    target_link_libraries(crackdown PRIVATE winmm)
else()
    add_executable(crackdown ${CRACKDOWN_SOURCES})
endif()

target_include_directories(crackdown PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/generated
)

target_link_libraries(crackdown PRIVATE rex::runtime)

# Platform entry point, ReXApp source, and platform-specific settings
rexglue_configure_target(crackdown GPU_PLUGINS xenos)
add_dependencies(crackdown crackdown_pack_fix)
# ReXGlue 0.10 emits guest SEH handlers requiring asynchronous exceptions.
if(WIN32 AND CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(crackdown PRIVATE -fasync-exceptions)
endif()
target_precompile_headers(crackdown PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/generated/crackdown_pch.h")

option(CRACKDOWN_BUILD_TESTS "Build Crackdown regression tests" OFF)
if(CRACKDOWN_BUILD_TESTS)
    enable_testing()
    add_executable(crackdown_enumeration_tests tests/enumeration_tests.cpp src/enumeration.cpp)
    target_include_directories(crackdown_enumeration_tests PRIVATE generated)
    target_link_libraries(crackdown_enumeration_tests PRIVATE rex::runtime)
    add_test(NAME enumeration_failure COMMAND crackdown_enumeration_tests)
    add_executable(crackdown_audio_barrier_tests tests/audio_barrier_tests.cpp src/audio_barrier.cpp)
    target_include_directories(crackdown_audio_barrier_tests PRIVATE generated)
    target_link_libraries(crackdown_audio_barrier_tests PRIVATE rex::runtime)
    add_test(NAME audio_barrier COMMAND crackdown_audio_barrier_tests)
    set_tests_properties(audio_barrier PROPERTIES TIMEOUT 60)
    if(WIN32)
        add_executable(crackdown_graphics_options_tests tests/graphics_options_tests.cpp src/graphics_options.cpp)
        target_include_directories(crackdown_graphics_options_tests PRIVATE src generated)
        target_link_libraries(crackdown_graphics_options_tests PRIVATE rex::runtime)
        add_test(NAME graphics_overlay_lifecycle COMMAND crackdown_graphics_options_tests)
        add_executable(crackdown_debugger tools/watch_game.cpp)
        target_link_libraries(crackdown_debugger PRIVATE dbghelp)
        add_executable(crackdown_automation_tests tests/automation_tests.cpp src/automation.cpp)
        target_include_directories(crackdown_automation_tests PRIVATE src)
        target_link_libraries(crackdown_automation_tests PRIVATE rex::runtime)
        add_test(NAME automation_controller COMMAND crackdown_automation_tests)
        add_executable(crackdown_controller_backend_tests tests/controller_backend_tests.cpp src/controller_input.cpp)
        target_include_directories(crackdown_controller_backend_tests PRIVATE src)
        target_link_libraries(crackdown_controller_backend_tests PRIVATE rex::runtime)
        add_test(NAME controller_backend_sdl COMMAND crackdown_controller_backend_tests false)
        add_test(NAME controller_backend_xinput COMMAND crackdown_controller_backend_tests true)
        set_tests_properties(controller_backend_sdl controller_backend_xinput PROPERTIES TIMEOUT 30)
    endif()
    add_executable(crackdown_cache_tests tests/cache_tests.cpp src/cache.cpp)
    target_include_directories(crackdown_cache_tests PRIVATE src)
    target_link_libraries(crackdown_cache_tests PRIVATE rex::runtime)
    add_test(NAME cache_filesystem COMMAND crackdown_cache_tests)
    add_test(NAME vector_packing
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/vector_packing_test.py"
                --generated "${CMAKE_CURRENT_SOURCE_DIR}/generated"
                --cxx "${CMAKE_CXX_COMPILER}")
    set_tests_properties(vector_packing PROPERTIES TIMEOUT 60)
    if(REXSDK_DIR)
        set(CRACKDOWN_SDK_INCLUDE "${REXSDK_DIR}/include")
    else()
        get_target_property(CRACKDOWN_SDK_INCLUDES rex::runtime INTERFACE_INCLUDE_DIRECTORIES)
        list(GET CRACKDOWN_SDK_INCLUDES 0 CRACKDOWN_SDK_INCLUDE)
    endif()
    add_test(NAME audio_math
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/audio_math_test.py"
                --generated "${CMAKE_CURRENT_SOURCE_DIR}/generated"
                --include "${CRACKDOWN_SDK_INCLUDE}"
                --cxx "${CMAKE_CXX_COMPILER}")
    set_tests_properties(audio_math PROPERTIES TIMEOUT 60)
    add_test(NAME spatial_audio
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/spatial_audio_test.py"
                --generated "${CMAKE_CURRENT_SOURCE_DIR}/generated"
                --include "${CRACKDOWN_SDK_INCLUDE}"
                --cxx "${CMAKE_CXX_COMPILER}")
    set_tests_properties(spatial_audio PROPERTIES TIMEOUT 60)
    add_custom_target(crackdown_check
        COMMAND "${CMAKE_CTEST_COMMAND}" --output-on-failure
        DEPENDS crackdown_cache_tests crackdown_audio_barrier_tests crackdown_enumeration_tests crackdown_pack_fix
        USES_TERMINAL
    )
    if(WIN32)
        add_dependencies(crackdown_check crackdown_graphics_options_tests)
        add_dependencies(crackdown_check crackdown_automation_tests)
        add_dependencies(crackdown_check crackdown_controller_backend_tests)
    endif()
endif()

# Codegen target - run 'cmake --build . --target crackdown_codegen'
add_custom_target(crackdown_codegen
    COMMAND $<TARGET_FILE:rex::rexglue> codegen ${CMAKE_CURRENT_SOURCE_DIR}/crackdown_config.toml
    COMMAND "${Python3_EXECUTABLE}" "${CRACKDOWN_PACK_FIX}" "${CMAKE_CURRENT_SOURCE_DIR}/generated"
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
    COMMENT "Generating recompiled code for crackdown"
    VERBATIM
)
