# Crackdown Recompilation

An experimental native Windows recompilation of **Crackdown (2007), TU0**. Campaign gameplay is now playable in hands-on testing, with working movie and gameplay audio, controller input, and 1440p rendering. Development and testing are ongoing; full campaign completion has not been verified.

This fork builds on [SkiddyToast/Crackdown](https://github.com/SkiddyToast/Crackdown) and uses [ReXGlue SDK 0.2.2](https://github.com/rexglue/rexglue-sdk/releases/tag/v0.2.2), pinned to that exact version. The latest tested changes are available on this fork's `main` branch.

## Current state — October 4, 2026

| Area | Available now |
| --- | --- |
| Campaign | Solo gameplay, movement, shooting, camera controls and driving have been exercised. Hands-on play reports stable frame rate and working sound. |
| Rendering | Original 1280×720 or 2560×1440 internal rendering, optional FXAA, and experimental 3840×2160 rendering, using the original game assets. |
| Launcher | Saved Windows graphics, FPS/performance overlay, startup, audio buffer and controller settings. |
| Audio | Fixes for silent movies, mixer stalls and audio disappearing after gunfire. Launchers reduce the measured runtime queue delay from about 340 ms to about 40 ms. |
| Controller | SDL controller support with rumble; tested with an OEM wired Xbox 360 controller over USB. Optional direct XInput polling is available for comparison. |
| Startup | Microsoft and Realtime Worlds movies skip by default. Campaign cutscenes remain available. |
| Diagnostics | Optional controller automation, renderer screenshots, Windows audio session inspection, and crash/hang debugging tools. |

The measured performance samples used a Ryzen 7 9800X3D and RTX 5070 Ti on Windows. They establish behavior on that system, not minimum requirements or performance on other hardware. Direct XInput and normal SDL polling felt similar in hands-on comparison; no controller-latency improvement is claimed.

Publication checks regenerated TU0 code in a clean checkout and passed all nine regression checks with SDK 0.2.2. Gameplay testing remains separate from those automated checks.

Known limits include intermittent startup/sign-in crashes, unverified full-campaign and save/reload reliability, and movie synchronization that still needs validation after the audio queue change. The Linux presets have not been validated. Higher rendering resolution uses the original textures; enhanced assets and a custom in-game graphics menu are future work.

## Requirements

- CMake 3.25 or newer and Ninja
- Clang (the Windows build was verified with Clang 19.1.5)
- Python 3.8 or newer
- ReXGlue SDK 0.2.2 ([installation guide](https://github.com/rexglue/rexglue-sdk/wiki/Getting-Started))
- On Windows, Visual Studio C++ Build Tools with the Clang component and Windows SDK
- A legally acquired copy of Crackdown (2007) for the Xbox 360

## Steps To Recompile

Clone this fork and enter the repository:

```powershell
git clone https://github.com/jack-kenney/Crackdown.git
cd Crackdown
```

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
For normal play, use one of the included launchers:

| Launcher | Internal rendering | Log |
| --- | --- | --- |
| `Launch-Crackdown.cmd` | Configurable; defaults to 2560×1440 | `out/launcher-game.log` |
| `Play-Crackdown.cmd` | 1280×720 | `out/crackdown.log` |
| `Play-Crackdown-1440p.cmd` | 2560×1440 | `out/crackdown-1440p.log` |

The launchers use the extracted `assets` directory, store user data in `out/userdata`, and default to an eight-block audio queue to reduce playback delay. Launching the executable directly retains the SDK's 64-block default unless `--audio_maxqframes=8` is supplied. Startup movie skipping is enabled by default in the executable. F4 opens the SDK settings overlay; options that require a restart should be set before relaunching.

## Windows settings launcher

Double-click `Launch-Crackdown.cmd` after building. It opens a Windows settings window with Graphics and Game & audio tabs. Choose your options and select **Launch game**; **Save settings** stores them without starting the game. Settings apply to the next session and are saved in `out/launcher-settings.json`. The executable defaults to the standard Release build; **Browse** can select a different build. Windows PowerShell 5.1 and Windows Forms are sufficient; no Python packages are required for this launcher.

| Setting | Choices and behavior |
| --- | --- |
| Render resolution | 720p, 1440p (default), or 4K (experimental). Changes internal rendering independently of window size. |
| Anti-aliasing | Original rendering (default), FXAA, or higher quality FXAA. Uses the SDK's final-image filter; does not replace the game's textures. |
| Fullscreen | Borderless fullscreen or a window (default). |
| Bloom / shadows | Independent disable switches; both retain original game behavior by default. |
| Lighting accuracy fix | Enables GPU resolve readback to improve visibility and color accuracy. Off by default because it can substantially reduce frame rate. |
| Light halos | Hide coronas that appear through walls (default). This disables those coronas rather than adding geometric occlusion. |
| FPS / performance graph | The game's diagnostic FPS display, or that display with its performance graph. Off by default. |
| Startup movies | Skip the Microsoft and Realtime Worlds movies (default); campaign cutscenes remain available. |
| Audio buffering | 8 blocks (default, ~43 ms capacity), 16 (~85 ms), or 64 (~341 ms). |
| Controller polling | Standard SDL (default) or direct XInput for Xbox controllers. |

VSync and the Windows timer precision request retain the tested timing settings. Changing these options requires a new game session. The launcher does not change an already running game.

Equivalent command-line flags include `--show_fps=true`, `--show_perfgraph=true`, `--disable_bloom=true`, `--disable_shadows=true`, `--fix_lighting=true`, `--fix_light_occlusion=false`, `--postprocess_antialiasing=fxaa` (or `fxaa_extreme`), and `--fullscreen=true`. The legacy `--misc_performance_improvements=true` disables both bloom and shadows; its inherited bloom write has been corrected so zero actually skips TU0's bloom pass.

Launcher validation exercises saved settings, invalid-setting rejection, Windows command-line quoting, and the actual Save/Launch buttons. Separate runtime checks captured 1440p gameplay with the FPS display and FXAA, and 4K output with higher quality FXAA. 4K remains experimental; those checks do not establish sustained city/combat performance.

## Rendering resolution

The Direct3D 12 renderer can render the game's 1280×720 image at 2560×1440 by scaling both axes by two. Launch the built executable with:

```powershell
out\build\win-amd64-release\crackdown.exe assets --draw_resolution_scale_x=2 --draw_resolution_scale_y=2
```

`Play-Crackdown-1440p.cmd` combines this resolution with the smaller audio queue and writes diagnostics to `out/crackdown-1440p.log`.

Use `1` for both values to restore 720p. These are rendering settings; resizing the window changes the displayed image size independently. A 1080p display can downsample the 1440p render. Scaling does not replace the game's original textures or movie assets. Both settings require restarting the game; the SDK's F4 settings overlay also exposes them under GPU.

A preliminary RTX 5070 Ti test captured real 2560×1440 renderer output and exercised shooting, movement, and camera rotation with working audio. Two 30-second stationary Agency garage samples measured 29.49 guest frame submissions/s at 720p and 29.44 at 1440p, with approximately 37.6 ms p95 submission intervals in both. Another game instance remained running during both samples. These results support trying 1440p, but do not establish city/combat performance or displayed-frame timing.

## Startup movies

`skip_intro_movies` is enabled by default. It skips the Microsoft (`MSGS.bik`) and Realtime Worlds (`RTW_Logo.bik`) startup movies using TU0's normal completion handler, which frees their preloaded data and advances the frontend sequence. The extracted movie files remain intact. Static middleware/legal cards, the title animation, and campaign, gang and boss cutscenes retain their original behavior.

Launch with `--skip_intro_movies=false` to restore the startup movies; restart after changing this option. Windows runtime checks reached the title screen with the option enabled and disabled, and reached 1440p campaign gameplay with it enabled after playing the campaign opening movie.

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

ReXGlue 0.2.2 defaults to 64 queued blocks of 256 samples at 48 kHz. A full queue holds approximately 341 ms of already mixed audio, delaying newly mixed gunshots and movie audio. The repository launchers default to `--audio_maxqframes=8`, reducing this capacity to approximately 43 ms without changing the decoder, mixer, sample rate or SDL callback size. This setting must be supplied at startup; restart to change it. For a direct launch:

```powershell
out\build\win-amd64-release\crackdown.exe assets --audio_maxqframes=8
```

Diagnostic callback timestamps on the tested Windows stereo device measured median mixer-submission-to-SDL-callback residence of 339.9 ms with 64 blocks and 39.5 ms with eight. A 60-second 1440p gameplay sample exercised repeated shots, weapon switching, movement and camera rotation: all 11,250 callbacks contained nonzero finite audio, with no empty-queue callbacks, and p95 residence was 49.3 ms. Separate intro and campaign-movie samples also had no empty-queue or nonfinite callbacks. Diagnostic instrumentation was confined to a separate executable; the launchers use the existing SDK implementation.

These measurements cover the runtime queue, not physical button-to-speaker latency, Windows/device buffering, perceptual movie sync or extended city combat. If a different system develops crackles, select more buffering in the settings launcher, try `--audio_maxqframes=16` (approximately 85 ms capacity), or restore `64` for comparison. Do not change the live SDK setting in the overlay: existing semaphore limits are established during startup.

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

This repository contains source code and build tools; supply the game files from your own legally acquired copy of Crackdown. Game assets, generated game code, and development executables are excluded from Git.

This project is not affiliated with Microsoft, Microsoft Game Studios, or the now defunct Realtime Worlds.
