# Crackdown Recompilation
***Crackdown Recompilation is unfinished and it should be expected to encounter issues.***
This fork uses [ReXGlue SDK 0.2.2](https://github.com/rexglue/rexglue-sdk/releases/tag/v0.2.2) to recompile Crackdown TU0. The installed SDK package is pinned to that exact version. The original project is [SkiddyToast/Crackdown](https://github.com/SkiddyToast/Crackdown).

## Requirements
- CMake 3.25 or newer and Ninja
- Clang (the Windows build was verified with Clang 19.1.5)
- Python 3.8 or newer
- ReXGlue SDK 0.2.2 ([installation guide](https://github.com/rexglue/rexglue-sdk/wiki/Getting-Started))
- On Windows, Visual Studio C++ Build Tools with the Clang component and Windows SDK
- A legally acquired copy of Crackdown (2007) for the Xbox 360

## Steps To Recompile
Extract your Crackdown TU0 disc into `assets`, with `default.xex` directly inside that directory. Title updates have not been validated. Assets and generated sources are excluded from Git.

On Windows, the helper initializes the Visual Studio compiler environment, runs code generation if needed, enables and runs the regression tests, and builds Release:
```powershell
.\build-local.ps1 -SdkPath C:\path\to\rexglue-sdk\win-amd64
```
Alternatively, set `REXGLUE_SDK_ROOT` to that installed SDK directory. In this development workspace the helper also recognizes the SDK under `../.tools/rexglue-v0.2.2/win-amd64`.

Use `-TestOnly` for the regression checks without building the game, and `-Regenerate` after changing the XEX or recompilation configuration. Close the game before rebuilding its executable.

For a manual build, add Clang, Ninja and the SDK's `bin` directory to `PATH`. On Windows, use an x64 Visual Studio development shell:
```powershell
rexglue codegen crackdown_config.toml
cmake --preset win-amd64-release -DCMAKE_PREFIX_PATH=C:/path/to/rexglue-sdk/win-amd64 -DCRACKDOWN_BUILD_TESTS=ON
cmake --build --preset win-amd64-release --parallel 8
cmake --build --preset win-amd64-release --target crackdown_check
```
The Linux presets use `clang-20` and `clang++-20`; these changes have been validated on Windows only.

Launch from the repository root:
```powershell
out\build\win-amd64-release\crackdown.exe assets
```
`Play-Crackdown.cmd` provides the same asset path, stores user data in `out/userdata`, and writes diagnostics to `out/crackdown.log`.

## Correctness fixes and regression checks

The RAM cache supports creating, extending and appending files, refreshes file-size metadata, safely handles directory metadata and I/O, and synchronizes shared data access. Closing a cache file releases its handle. Its data remains available to other open handles.

ReXGlue 0.2.2 emits aliased `vpkd3d128 FLOAT16_4` conversions that overwrite a source sign bit before reading it. `tools/fix_vector_packing.py` snapshots the source vector while preserving the SDK's conversion and destination lanes. The workaround follows the approach in [BChapmanDev's upstream migration PR](https://github.com/SkiddyToast/Crackdown/pull/1); this fork applies it to SDK 0.2.2.

CMake runs the idempotent workaround before compiling the game and after the `crackdown_codegen` target. Unknown emitter output stops the rewrite before any files are changed. Generated C++ is never committed.

The cache tests cover growth, append, zero-filled gaps, EOF, overflow, directory operations, access permissions, metadata, open-handle lifetime, and concurrent reads/writes/resizing. The packing test compiles instructions from the generated game code and checks every finite half-float value in each lane, signed zero, subnormals, and preserved destination lanes. It also checks rewrite idempotence, newline preservation, and rejection of unexpected output.

These tests establish the repaired behaviors. Full campaign completion, save/reload reliability, and visual fidelity still require gameplay validation.

## Legal Stuff
This project is only inteded for use with legally acquired copies of Crackdown.
This project is not affiliated with Microsoft, Microsoft Game Studios, or the now defunct Realtime Worlds.
