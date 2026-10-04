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
The launcher also selects an eight-block audio queue to reduce playback delay; launching the executable directly retains the SDK's 64-block default unless `--audio_maxqframes=8` is supplied.

## Rendering resolution

The Direct3D 12 renderer can render the game's 1280×720 image at 2560×1440 by scaling both axes by two. Launch the built executable with:

```powershell
out\build\win-amd64-release\crackdown.exe assets --draw_resolution_scale_x=2 --draw_resolution_scale_y=2
```

`Play-Crackdown-1440p.cmd` combines this resolution with the smaller audio queue and writes diagnostics to `out/crackdown-1440p.log`.

Use `1` for both values to restore 720p. These are rendering settings; resizing the window changes the displayed image size independently. A 1080p display can downsample the 1440p render. Scaling does not replace the game's original textures or movie assets. Both settings require restarting the game; the SDK's F4 settings overlay also exposes them under GPU.

A preliminary RTX 5070 Ti test captured real 2560×1440 renderer output and exercised shooting, movement, and camera rotation with working audio. Two 30-second stationary Agency garage samples measured 29.49 guest frame submissions/s at 720p and 29.44 at 1440p, with approximately 37.6 ms p95 submission intervals in both. Another game instance remained running during both samples. These results support trying 1440p, but do not establish city/combat performance or displayed-frame timing.

## Correctness fixes and regression checks

The RAM cache supports creating, extending and appending files, refreshes file-size metadata, safely handles directory metadata and I/O, and synchronizes shared data access. Closing a cache file releases its handle. Its data remains available to other open handles.

ReXGlue 0.2.2 emits aliased `vpkd3d128 FLOAT16_4` conversions that overwrite a source sign bit before reading it. `tools/fix_vector_packing.py` snapshots the source vector while preserving the SDK's conversion and destination lanes. The workaround follows the approach in [BChapmanDev's upstream migration PR](https://github.com/SkiddyToast/Crackdown/pull/1); this fork applies it to SDK 0.2.2.

CMake runs the idempotent workaround before compiling the game and after the `crackdown_codegen` target. Unknown emitter output stops the rewrite before any files are changed. Generated C++ is never committed.

The cache tests cover growth, append, zero-filled gaps, EOF, overflow, directory operations, access permissions, metadata, open-handle lifetime, and concurrent reads/writes/resizing. The packing test compiles instructions from the generated game code and checks every finite half-float value in each lane, signed zero, subnormals, and preserved destination lanes. It also checks rewrite idempotence, newline preservation, and rejection of unexpected output.

These tests establish the repaired behaviors. Full campaign completion, save/reload reliability, and visual fidelity still require gameplay validation.

Intermittent startup/sign-in crashes are still under investigation. A successful play session does not establish startup reliability.

## Windows frame pacing

The game requests 1 ms timer precision before creating ReXGlue's runtime workers and keeps the request alive through runtime shutdown. This improves the precision of the SDK's emulated vertical-blank and command-processor sleeps. Successful requests are paired with `timeEndPeriod(1)` when the application is destroyed; a failed request is logged and startup continues.

Use `--high_resolution_timer=false` for comparisons with the original timing. Changing this setting requires a restart. Modern Windows applies timer requests per process and may reduce precision for an occluded or minimized window; see [Microsoft's timer API documentation](https://learn.microsoft.com/en-us/windows/win32/api/timeapi/nf-timeapi-timebeginperiod).

A controlled 30-second garage experiment on a Ryzen 7 9800X3D / RTX 5070 Ti measured 20.2 guest FPS before the request, 29.5 with it, and 19.8 after restoring the original state. The p95 submission interval fell from 77.7 to 37.7 ms. These are warm garage frame submissions, not displayed-frame latency or a guarantee of city/combat performance.

## Local game automation (Windows)

Launch with `--automation=true` to give the local test controller player one. Ordinary launches use the SDK's SDL controller input. Automation accepts Xbox buttons, both sticks, and triggers through a process-specific Windows shared-memory mapping; it requires no keyboard focus or virtual-controller driver. Controls release when their one-second lease expires if the sender stops.

Normal controller input acquires SDL's joystick lock before calling the SDK input driver. SDL controller event watches already hold that lock before acquiring the SDK controller-state mutex; matching this order prevents a deadlock during rumble and capability queries. Rumble remains enabled. A regression test exercises 50,000 concurrent events and rumble requests against the real SDL lock; removing the ordering wrapper reproduces the hang.

On Windows, `--sdl_direct_xinput=true` selects SDL's XInput backend for Xbox controllers by disabling its Raw Input backend before initialization. This reads Xbox pad state during the game's controller query instead of depending on delivery through a Windows message queue. SDL continues to handle mappings, menu keystrokes, hotplug and rumble. The default is `false`; changing it requires restarting. Other SDL controller backends remain available. Regression checks exercise buttons and release packets, signed stick endpoints, independent triggers, menu events, rumble and disconnect using an SDL virtual controller in both modes. A connected Microsoft wired Xbox 360 pad was confirmed to change from SDL's `r` backend tag to `x`.

This is an input-latency experiment, with no measured physical-button-to-screen improvement claimed yet. Instrumented TU0 garage gameplay polls input about once per 34 ms frame; its command processor also waits for an emulated vertical blank just before presenting. Direct controller polling does not remove those frame and presentation delays. ReXGlue's `--vsync=false` also accelerates the emulated vertical-blank timer and changes command-processor waits, so it is not used as an input-latency preset.

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

For a freeze with no exception, leave the game running and capture a diagnostic dump:

```powershell
out\build\win-amd64-release\crackdown_debugger.exe --dump $game.Id out\hang.dmp
```

This records thread stacks, registers and memory mappings without attaching a debugger. It excludes the guest heap to keep the dump small. Choose a new filename for each capture; existing dumps are never overwritten. Preserve the matching executable, PDB, map and log alongside it.

The Windows automation regression test checks controller-state byte order, button events for menus, acknowledgement, rejection of partially published input, and automatic release with a changed packet number when the sender's lease expires. Manual automation has also reached Campaign Solo gameplay and exercised movement, jumping, and camera input.

## Windows audio diagnostics

ReXGlue 0.2.2 defaults to 64 queued blocks of 256 samples at 48 kHz. A full queue holds approximately 341 ms of already mixed audio, delaying newly mixed gunshots and movie audio. Both repository launchers use `--audio_maxqframes=8`, reducing this capacity to approximately 43 ms without changing the decoder, mixer, sample rate or SDL callback size. This setting must be supplied at startup; restart to change it. For a direct launch:

```powershell
out\build\win-amd64-release\crackdown.exe assets --audio_maxqframes=8
```

Diagnostic callback timestamps on the tested Windows stereo device measured median mixer-submission-to-SDL-callback residence of 339.9 ms with 64 blocks and 39.5 ms with eight. A 60-second 1440p gameplay sample exercised repeated shots, weapon switching, movement and camera rotation: all 11,250 callbacks contained nonzero finite audio, with no empty-queue callbacks, and p95 residence was 49.3 ms. Separate intro and campaign-movie samples also had no empty-queue or nonfinite callbacks. Diagnostic instrumentation was confined to a separate executable; the launchers use the existing SDK implementation.

These measurements cover the runtime queue, not physical button-to-speaker latency, Windows/device buffering, perceptual movie sync or extended city combat. If a different system develops crackles, try `--audio_maxqframes=16` (approximately 85 ms capacity) or restore `64` for comparison by editing the launcher or launching the executable directly with that value. Do not change the live SDK setting in the overlay: existing semaphore limits are established during startup.

If sound is missing, inspect the game's actual Windows audio session while it is running:

```powershell
python -m pip install pycaw psutil
python tools/check_audio.py
```

Use `--pid <PID>` to select a specific process and `--seconds 10` for a longer sample. The tool reports active output devices, the default multimedia device, endpoint/session volume and mute settings, and each game session's peak signal. It only reads settings. Exercise an in-game sound, such as firing a weapon, during sampling; a silent menu or loading screen alone does not establish an audio failure.

A nonzero session peak shows audio reaching that Windows output device, but does not confirm that the connected speakers or headphones are audible. Check that the reported device matches the one you are listening to. A zero peak while sounds should be playing warrants inspecting the runtime's `audio_mute` setting and the audio log.

Two TU0 audio fixes are applied by default. The mixing workers use a native barrier with a generation counter: the guest barrier could clear a worker's arrival for the next round and leave audio permanently stalled. `--fix_audio_barrier=false` restores the guest implementation for comparison; restart after changing this setting. The build also corrects two reciprocal square-root instructions in Bink decoder initialization. ReXGlue 0.2.2's lookup table produced an overflowing estimate, resulting in a zero normalization gain and invalid scratch allocation sizes. The correction uses a host reciprocal square root at those two sites and is reapplied after code generation.

Regression checks exercise repeated and consecutive mixing rounds with uneven workers, and execute the generated Bink initializer for all block sizes in mono/stereo DCT and RDFT modes. Runtime testing confirmed campaign cutscene audio reaching the Windows stereo output and gameplay audio continuing after the movie transition. The `.bik` movies and audio banks load from their expected extracted asset paths; no asset renaming is required. Other output devices and extended play sessions still need validation.

The TU0 positional-audio vector angle helper also clamps normalized dot products to `[-1, 1]`. Gameplay tracing captured both `0x3F800001` and `0xBF800001` (one floating-point step outside the domain) entering the helper. Its reciprocal square-root refinement then generated NaNs; the spatial mixer passed these into persistent reverb history, silencing the Windows stereo output even while render frames continued to submit normally. The clamp retains the original guest approximation. A regression executes the actual generated helper with the captured inputs and checks that 20,001 valid four-lane inputs remain bit-for-bit identical.

## Legal Stuff
This project is only inteded for use with legally acquired copies of Crackdown.
This project is not affiliated with Microsoft, Microsoft Game Studios, or the now defunct Realtime Worlds.
