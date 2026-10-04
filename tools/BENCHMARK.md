# Repeatable Windows benchmarks

`benchmark_game.py` records the wired Xbox 360 controller, replays a timed input
route, or sweeps the ordinary gameplay camera while the player stays in a busy
area. It uses standard-library Python and the existing `--automation=true`
interface. No virtual controller, debugger attachment, or game rebuild is needed.

The first useful route is: load the Agency garage, walk to the same Agency car,
enter it, then drive out through a chosen populated area. Record that drive once;
replay it from the same starting state for each graphics preset. This tooling
does not yet include a calibrated route or automatic menu/loading detection.

## Prepare a baseline and sampler

Run commands from the repository root. Close the game before copying its save
profile so the baseline contains a consistent save. Keep that baseline unchanged
for comparisons; the launcher copies it into a fresh session every time.

```powershell
New-Item -ItemType Directory -Force out/benchmark | Out-Null
Copy-Item -LiteralPath out/userdata -Destination out/benchmark/profile-baseline -Recurse
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare-benchmark.ps1
```

Create the baseline once, at a new destination. Copying onto an existing directory
can nest or merge profiles. To choose another baseline, use `-ProfileSource` below.
The sampler helper needs the same Visual Studio C++ Build Tools and ReXGlue SDK
headers used to build this project. `-SdkPath` selects another installed SDK.
Offsets are generated from those headers and bound to the SHA256 hashes of both
SDK DLLs. Sampling rejects different binaries and non-D3D12 command processors.
Release SDK 0.10.0 disables its internal per-frame performance counters.

## Load and position the player

```powershell
$session = & tools/start-benchmark.ps1 -ResolutionScale 2
python tools/benchmark_game.py live --pid $session.GameProcessId
```

The launcher uses your saved graphics settings, with a window by default. Optional
parameters are `-ResolutionScale 1|2|3`, `-Antialiasing none|fxaa|fxaa_extreme`,
`-FixLighting $true|$false`, `-Bloom $true|$false`, `-Shadows $true|$false`,
`-Fullscreen $true|$false`, and `-ProfileSource <directory>`. It writes the complete
arguments/settings to the session's `launch.json` and a separate `game.log`.
It does not change launcher preferences or your regular saves.

Automation replaces physical player-one input. The `live` command bridges your
real controller into that interface so you can operate menus and position the
player. Load Campaign Solo, wait for loading to finish, and choose a reproducible
garage position and camera direction. Press Ctrl+C in the terminal to stop the
bridge and release input. Use one input sender per game instance. Physical
controller index defaults to zero; `--controller 1` selects another XInput slot.

## Record the garage-to-city drive

```powershell
python tools/benchmark_game.py record --pid $session.GameProcessId --bridge --route out/benchmark/agency-drive.json --seconds 90 --warmup 10 --output "$($session.RunDirectory)/recording" --note 'Agency garage to chosen city area, 1440p'
```

After a three-second countdown, walk to the car, enter it, and drive your route.
The recorder continues forwarding the real controller. It records changes in raw
XInput state at 100 Hz, including buttons, sticks, and triggers. The first ten
seconds are recorded but excluded from frame statistics. Adjust `--warmup` to
exclude your garage setup. Loading before the recording is excluded entirely.
`--countdown 5` gives more preparation time. Durations are limited to ten minutes.
Use a new route filename; existing recordings and result directories are protected
against overwriting.

You can also record in a normal game instance: omit `--bridge`, supply that game's
`--pid`, and use your controller normally. Without `--pid`, recording saves inputs
without performance measurements. The automation bridge is recommended for
comparing recording and replay through the same input path.

## Replay from the baseline

Close the benchmark instance, launch another from the same baseline, then use
`live` to reach exactly the same starting position and orientation. Stop `live`.

```powershell
$session = & tools/start-benchmark.ps1 -ResolutionScale 1
# Run live, load/position, then Ctrl+C before replay:
python tools/benchmark_game.py live --pid $session.GameProcessId
python tools/benchmark_game.py replay --pid $session.GameProcessId --route out/benchmark/agency-drive.json --output "$($session.RunDirectory)/measurement" --note '720p comparison'
```

Repeat with resolution scale 2 or 3, or lighting/bloom/shadow overrides. Replay
uses the original warmup unless `--warmup` overrides it. Compare runs that actually
followed the intended route. Timed controller replay does **not** restore all
world state, random seeds, traffic, physics, or shader-cache state. A collision or
different enemy encounter can send the car off route. Menu/loading sequences
should be completed manually before the timed run because their duration varies.
Reload the baseline between runs; replay does not reset the game itself.

## More repeatable graphics test: a camera sweep

Use `live` to stand on a safe rooftop or overlook in a populated area, with the
same starting viewpoint for each run. Stop the bridge, then run:

```powershell
python tools/benchmark_game.py sweep --pid $session.GameProcessId --seconds 60 --warmup 5 --output "$($session.RunDirectory)/sweep" --note 'Populated city overlook, 1440p'
```

The player stays put while the right stick alternates horizontal camera motion
every ten seconds. `--speed 12000` and `--segment 10` adjust this. This is the
ordinary gameplay camera, not a detached free-flight camera. Keeping the player
in the area retains its streaming and simulation workload. A future free camera
would need to handle streaming focus too; flying a camera away from a stationary
player would not necessarily exercise that area's enemies and traffic.

## Interpret results

Each measured run writes `frames.csv` (host elapsed seconds, guest frame counter,
and completed GPU frame counter) and `summary.json`. The summary contains average
guest submission FPS, p50/p95/p99 intervals, intervals over 50 ms, unobserved frame
transitions, longest observed submission gap, CPU use in logical-core equivalents,
and input scheduling lateness. Warmup is excluded. Failed/interrupted runs have
`complete: false`; do not compare them as finished routes. An interrupted recording
is preserved with that flag; summaries retain its completion flag and route file
hash on playback. Screenshots are deliberately excluded from the timed
interval because renderer readback can stall it.

Frame timestamps come from read-only polling at approximately two milliseconds;
they have polling/scheduling uncertainty and describe **guest D3D12 submissions**,
not displayed frames, GPU work duration, or input-to-screen latency. Intervals
whose frame counter jumped by more than one are excluded from percentiles and
reported separately. First/last partial frame intervals are excluded too.

The game normally runs at 30 FPS. Two presets can both sustain 30 FPS while using
very different GPU headroom; this sampler cannot measure that headroom. Keep
`vsync=true`: disabling it in this SDK can change guest pacing and game speed.
Compare at least three successful runs of each preset, alternate preset order,
and distinguish cold streaming/shader-cache runs from warmed runs. For a fair
hardware comparison, close other active game instances and keep background load
similar. The session profile copy includes its baseline shader cache.

Ctrl+C, disconnection, and exceptions release injected controls; a 250 ms input
lease also expires if the sender disappears. Replay/sweep abort after three
seconds without a game input acknowledgement. The game remains open and ordinary
physical input remains replaced until you run `live` again or restart normally.

## Initial validation

On SDK 0.10.0, a separate instance exercised physical XInput recording, automation
replay, live bridge interruption/release, DLL identity rejection, and a gameplay
camera sweep. A 12-second 720p Agency garage sweep with two seconds of warmup
reported 300 submissions in the measured ten seconds, p95 35.24 ms, p99 37.33 ms,
and no intervals over 50 ms. These are short tool smoke checks, not a heavy-city
benchmark or a comparison of graphics presets. All eleven repository regression
checks passed, including route safety and interrupted-recording cleanup.
