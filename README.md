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

Intermittent startup/sign-in crashes are still under investigation. A successful play session does not establish startup reliability.

## Local game automation (Windows)

Launch with `--automation=true` to give the local test controller player one. Ordinary launches use the SDK's SDL controller input. Automation accepts Xbox buttons, both sticks, and triggers through a process-specific Windows shared-memory mapping; it requires no keyboard focus or virtual-controller driver. Controls release when their one-second lease expires if the sender stops.

```powershell
$game = Start-Process out\build\win-amd64-release\crackdown.exe -ArgumentList 'assets --automation=true --user_data_root=out/automation-userdata --log_file=out/automation.log --enable_console=false' -PassThru
python tools/control_game.py --pid $game.Id --press start --seconds 0.25
python tools/control_game.py --pid $game.Id --ly 24000 --seconds 1
python tools/control_game.py --pid $game.Id --rx 12000 --seconds 0.5
python tools/control_game.py --pid $game.Id --capture
```

`--press` accepts `a b x y start back up down left right lb rb ls rs`, including combinations. Stick axes range from -32768 to 32767; `--lt` and `--rt` range from 0 to 255. Use one sender per game process. Running the tool with only `--pid` releases the controller. Captures come directly from the game renderer and are saved under `out/automation/<PID>`; install Pillow (`python -m pip install Pillow`) to convert the RGB captures to PNG. A different user-data parent directory needs a matching `--directory` for captures.

Release builds retain `crackdown.pdb` and `crackdown.map` for crash analysis. With regression tests enabled, the build also produces a small native debugger:

```powershell
out\build\win-amd64-release\crackdown_debugger.exe $game.Id > out\debugger.log
```

It attaches to the specified process and prints function names, fault addresses, and stack traces for unhandled exceptions. Ctrl+C detaches. Expected first-chance GPU memory-protection faults are passed to the runtime without flooding the log. Keep the matching executable, PDB, map, and runtime log when investigating a crash. A debugger can change timing, so a successful attached run does not establish startup reliability.

The Windows automation regression test checks controller-state byte order, button events for menus, acknowledgement, rejection of partially published input, and automatic release with a changed packet number when the sender's lease expires. Manual automation has also reached Campaign Solo gameplay and exercised movement, jumping, and camera input.

## Legal Stuff
This project is only inteded for use with legally acquired copies of Crackdown.
This project is not affiliated with Microsoft, Microsoft Game Studios, or the now defunct Realtime Worlds.
