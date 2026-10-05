# Crackdown TU0 high frame rate investigation

Status, 2026-10-04: the [native timestep prototype](#native-timestep-prototype)
now measures about 59.5 guest FPS with near-baseline walking speed. Jump physics
and other timing-dependent systems still need validation. The normal build stays
at its original pacing. The initial pacing-only experiment below is retained as
a negative control: it accelerates gameplay.

`fps60.cpp` is excluded from the default build. Only an isolated build configured
with `CRACKDOWN_BUILD_EXPERIMENTS=ON` includes it. Its
`--fps60_pacing_experiment=true` flag changes the D3D swap scheduling callback
`0x826BC3D0` from interval two to interval one and raises a separate render-worker
sleep cap to 60. It leaves the SDK's 60 Hz guest video refresh and VSync enabled.
Do not add this option to the regular launcher until gameplay timing is repaired.

## Limiter findings

- `0x826BC3D0` extracts the presentation interval from callback argument bits
  8 through 11 and schedules a swap relative to the guest vblank counter.
- `0x826BC2D0` services the pending swap queue on guest vblank interrupts.
- `0x826BC568` reads the D3D device's presentation interval at device offset
  13220, maps interval two to a two-vblank delay, and packs that callback argument.
  This offset comes from the copied presentation parameters (offset 52).
- Guest word `0x82BAA330` is a second, integer millisecond render cap. The render
  worker `0x826A65F0` and render completion path `0x826A7698` can sleep for
  `1000 / cap` milliseconds. The tested live process had cap 32 and interval two.
- SDK 0.10's `GraphicsSystem::SetupGuestGpu` obtains the guest vblank frequency
  from the configured video mode. Disabling `vsync` selects roughly 1000 Hz guest
  interrupts instead; that is unsuitable for a correct gameplay frame-rate fix.

The upstream [Xenia Canary TU0 patch file](https://github.com/xenia-canary/game-patches/blob/main/patches/4D5307DC%20-%20Crackdown%20%28TU0%29.patch.toml)
contains graphics/debug patches, but no existing 60 FPS patch to port as of the
date above.

## Controlled measurements

Tests used an owned automation instance with a copied `out/upgrade-userdata`
profile, windowed 720p, original bloom and shadows, lighting fix off, and FXAA
Extreme inherited from the saved settings, with the saved performance graph on.
The owned session was PID 43088, launched through `tools/start-benchmark.ps1`
at resolution scale one with `FixLighting` false, using session directory
`out/benchmark/session-20261004-164137-4549`. The player stood in the Agency garage
on foot; the initial displayed player coordinates were approximately
1894.51, 10.92, -1821.82. All three phases used the same running session.
The normal executable and SDK DLLs were not rebuilt or replaced. Temporary guest
data changes were restored after each test, including on Python exceptions.

Frame measurements use the existing read-only D3D12 guest submission probe,
not displayed frame times. Each phase measured 15 seconds after two seconds of
settling. These controlled A/B prototypes were tested by changing the
corresponding guest data.

| Experiment | Baseline FPS | Experimental FPS | Restored FPS |
|---|---:|---:|---:|
| Render cap 32 to 60 only | 29.9985 | 29.9961 | 29.9991 |
| Present interval two to one only | 29.9991 | 31.6629 | 29.9981 |
| Interval one and render cap 60 | 29.9983 | 56.5247 | 29.9954 |

The combined experiment had median submission intervals of 17.561 ms, versus
32.652 ms before and 32.641 ms after. These are polling measurements on this
machine and scene, not a claim of sustained city performance.

A subsequent build using `build-local.ps1 -Experiments` compiled both the
callback hook and the camera prototype into a separate executable. With its
FPS flag explicitly enabled, it reached garage gameplay at 1440p, 16x filtering,
original anti-aliasing and FOV scale 1.2. Its hook logged activation, the render
cap read 60, and a five-second camera sweep (one-second warmup, four measured)
submitted 55.03 FPS. This establishes that the compiled prototype activates;
it does not repair the gameplay-speed issue found below. That owned instance
closed normally. Its local artifacts are under
`out/camera-research/runtime-compiled-fps`, including launch arguments,
renderer capture, frame CSV and summary JSON.

## Gameplay speed checks

The same instance then received one second of full forward stick followed by
two seconds for the camera to settle. A reverse input returned it near the
starting point before the next phase. Horizontal displacement came from the
camera's world transform translation at camera offsets 320 and 328.

| Phase | Camera displacement after 1 second of movement |
|---|---:|
| Baseline | 7.3952 world units |
| Interval one and render cap 60 | 14.0232 world units |
| Restored | 7.4421 world units |

The experimental movement was approximately 1.90 times the baseline, tracking the
approximately 1.88 times submission rate. This is evidence that removing these
limiters alone accelerates the game's simulation.

A separate stationary jump with A held for 0.12 seconds corroborated the timing
change. Camera return time fell from 1.174 seconds to 0.814 seconds and returned
to 1.180 seconds after restoration. Camera peak heights changed too. This is a
camera observation; it does not independently measure the character's physics.

Raw local outputs are `out/fps-cap-experiment.json`,
`out/fps-interval-experiment.json`, `out/fps-combined-experiment.json`,
`out/fps-walk-experiment.json`, and `out/fps-jump-experiment.json`. They are ignored
local test artifacts, not game assets.

## Static timestep audit

The shared float at guest `0x820ED704` is 1/30 and at `0x820ED634` is 1/60.
`0x8254C568` is a widely used game delta-time getter: depending on engine state it
returns a scaled 1/30 when byte 13 in the engine object at
`BE32[0x82DE25C0]` is nonzero. Otherwise it selects a scaled 1/60 or another
clock-derived value. Its scale comes from float `0x82C63578` (structure
`0x82C63564` plus 20). The generated code contains 95 direct getter callsites
across 73 functions. Confirm the live branch and invocation rates before changing
its return value.

A concrete displacement path in the generated code is:

1. `0x82190908` calls `0x82158CA0`, returning at guest LR `0x82190B18`.
2. `0x82158CA0` obtains dt from `0x8254C568` at return LRs `0x82158EB8` and
   `0x8215901C`, depending on its branch.
3. It broadcasts dt to a vector, multiplies a movement vector by dt, adds this
   displacement to the current position obtained through virtual-table offset
   12, and forwards a new transform through virtual-table offset 172.
4. The same parent `0x82190908` also calls `0x82191C88`. That routine obtains dt
   at LR `0x82191DAC` and compares displacement length against speed times dt.

This is actual displacement arithmetic, not just a timer. It makes the getter
the narrowest shared timestep candidate identified so far. The object class and
whether this branch updates the local player in the measured garage scene are
not yet proven. Indirect dispatch prevents a complete static chain from the main
loop to this particular movement path. Instrument the three listed guest LRs,
with object identities and movement input recorded, to resolve that gap.

The render completion path `0x826A7698` also computes a scaled 1/30 before
calling `0x826A6B50`. However, that function only forwards the incoming `f1` to
dispatcher `0x823241A8`. The dispatcher does not explicitly store or use dt;
other calls and virtual dispatch occur before its callbacks. Changing this
single incoming register is therefore **not** a demonstrated central simulation
fix: the displacement routines above obtain dt independently from the getter.

A first isolated simulation experiment could override the getter's return only
for the three movement-related LRs above, only in the verified 30 Hz engine
branch, while preserving time scale and the clock-derived branches. This would
test the causal movement hypothesis, not create a complete 60 FPS patch. A
broader shared-getter override still needs a fixed-step policy plus physics,
animation, camera, weapon, and mission-timer audits.

There are direct 1/30 consumers as well. For example, `0x8252EDA0` increments an
object timer at offset 2404 by this constant on each call. Other consumers include
`0x8228B0F0` and `0x821A48A8`. Their invocation rates and roles need auditing
before replacing constants globally. At least one 1/30 use converts a frame count
to elapsed seconds (`0x82265C70`), so global replacement could alter frame-count
metadata independently of simulation cadence.

## Original follow-up plan (superseded by native timing work below)

Next steps:

1. Instrument update `0x826A6B50` and the delta-time getter, capturing call rate,
   supplied/returned dt, and accumulated simulation time against host time.
2. Separate simulation cadence from presentation cadence, or repair the shared
   timestep and the direct fixed-step consumers where required.
3. Repeat the fixed-duration movement and jump checks from the same save. Add
   vehicle acceleration/braking, weapon fire intervals, animation, and mission
   timer checks before testing recorded city routes.
4. Check heavy-area CPU/GPU capacity at 720p and 1440p once behavior matches the
   30 FPS baseline. Validate loading, cutscenes, audio synchronization, and saves.

`fps60_tests.cpp` originally covered ten cases for the disabled default, preserved
callback fields (including the 64-bit high word), and intervals zero/one/three/15
remaining unchanged. It checks the prototype's guard behavior, not gameplay
correctness. Keep any further FPS experiments out of the stable executable.

## Native timestep prototype

Double-click `Play-Crackdown-60FPS-Experimental.cmd` to launch the experimental
60 FPS target using your saved launcher graphics settings. On first launch it
copies `out/userdata` into `out/userdata-fps-experimental`; later runs retain
that separate profile. Logs go to `out/native-timing-game.log`.

Build separately with `./build-local.ps1 -Experiments`. The executable is
`out/build/win-amd64-experiments/crackdown.exe`. Add `--native_frame_rate=60`
to an otherwise normal launch, retaining `--vsync=true`. Other accepted targets
are `120`, `144`, and `240`; `0` keeps original behavior. These are pacing targets,
not guarantees of achievable performance. Use a copied test profile through
`--user_data_root`, because gameplay correctness is still under investigation.
The native option takes precedence over the older pacing-only flag. None of
these hooks are included in the normal executable or launcher.

The prototype activates existing elapsed-time machinery rather than returning
an arbitrary fixed 1/60 from the delta getter:

- Engine `BE32[0x82DE25C0]`, byte `+13`, selects fixed timing. Clearing it and
  setting minimum/maximum milliseconds at `+40/+44` to `1/100` lets timer
  `0x823263D8` use the kernel millisecond clock. The default branch overwrites
  both limits with 33 every call.
- Commit `0x82326348` publishes elapsed milliseconds at `0x82D99128` and the
  native-time flag at `0x82DE3FB0`. It advances committed time by the consumed
  delta, retaining residual time after a capped update. Large gaps have their
  own original 2000 ms discard behavior. The newer hitch policy below discards
  excess time before this commit; disabling that policy restores this behavior.
- Getter `0x8254C568` consumes that native delta. Havok independently selects
  the same milliseconds in `0x8248F210`, then calls `0x82492118`,
  `0x82980AE0`, and simulation step `0x829BD048`. Hooking only the getter would
  leave physics at 33 ms.
- Main loop `0x826A7698` overwrites the timer output with scaled 1/30 before
  `0x826A6B50`. The prototype corrects that specific call's incoming delta,
  preserving its original scale at `0x82CFE4B4`.
- Immediate presentation clears only interval bits 8..11 of `0x826BC3D0`'s
  callback argument. Raw D3D enum zero means one vblank; raw `0x80000000`
  means immediate. The callback uses encoded interval zero for immediate.
  Cap word `0x82BAA330=1001` produces an integer sleep budget of zero; writing
  zero to this word would trap. The SDK guest vblank remains at 60 Hz.
- A host steady-clock deadline paces the timer at the selected target. It
  retains cadence through small oversleeps and resets after missed slots.
  Zero native deltas receive up to three 1 ms retries of the actual timer.
  If zero persists, a warning is logged and the core update is skipped through
  engine pause byte `+14`; housekeeping remains active.
- Native mode otherwise stops engine frame counter `+16`. Some consumers
  schedule exact-equality deadlines such as counter+60. The prototype keeps a
  logical 30 Hz counter from unpaused elapsed time, advancing at most one index
  per update and retaining backlog so deadlines cannot be skipped. This needs
  extended loading, pause, save, and mission testing.

### Measured behavior

Windows, ReXGlue 0.10.0, 720p, original AA, 4x filtering, original lighting,
VSync enabled, no graphics overlay. Each instance used its own copied profile.
Measurements count guest submissions, not physical monitor presentations.

First, reversible live changes tested the native timer without the compiled
update/counter/pacer hooks. One-second forward input followed by two seconds of
settling gave these camera displacements:

| Mode | Guest FPS | Displacement |
|---|---:|---:|
| Original | 29.99 | 7.395 |
| Native timing, original pacing | 29.99 | 7.448 |
| Native timing, immediate, cap 60 | 57.66 | 7.623 |
| Native timing, immediate, cap 120 | 58.32 | 7.578 |
| Native timing, immediate, cap 1001 | 60.66 | 7.451 |
| Original restored | 29.99 | 7.438 |

The earlier pacing-only patch moved 14.023 units. This establishes a substantial
correction of the approximately doubled walking speed, with input sampling and
camera settling still contributing measurement variation.

Direct physics position is `float3[BE32[player+292]+288]`, where
`player=BE32[0x82DE3F3C]`. Do not read its fourth padding lane as a coordinate.
For a 0.12-second A press, the original jump peaked at 1.7167 units and landed
after 1.1819 seconds. Native timing peaked at 1.6660 and landed after 1.1501;
restoring original timing gave 1.7167 and 1.1882. The roughly 3% height difference
is unresolved; smaller variable physics steps may change numerical integration,
and input polling can also affect the result.

The compiled prototype then booted through profile selection and the campaign
movie into the garage. A ten-second direct-position walk/return/jump test gave:

| Target | Guest FPS | Logical counter Hz | Walk distance | Jump height | Landing time |
|---|---:|---:|---:|---:|---:|
| 60 | 59.48 | 29.89 | 7.312 | 1.6614 | 1.1538 s |
| 240 | 59.49 | 29.99 | 7.256 | 1.6566 | 1.1591 s |

The 240 target reached about 240 updates/s in menus. Garage gameplay remained
around 56–61 FPS. A five-second CPU sample used about 0.92 core on the main guest
thread; GPU utilization was approximately 32%. Main-thread instrumented event
waits accounted for only about 6% of wall time in a nearby ten-second window.
Subsequent stack sampling found the main thread mostly polling graphics progress,
while the graphics command thread used a core. See the performance work below.
120/144 targets pass pacing-policy tests but have not had separate gameplay runs.

One-second traces show clock commits and main updates consuming approximately
one second of simulation time per real second. Optional
`--timing_trace_path=<absolute CSV path>` aggregates getter calls by caller and
thread, main updates, commits, sleep requests, actual sleeps, event waits, and
raw presentation enums. Wait durations are charged when a wait returns, so a
long wait can span several reporting windows; do not interpret its row as that
window's utilization. Tracing adds overhead and should be disabled for final
performance comparisons.

Local raw outputs are under `out/timing/session-20261004-172141`,
`out/timing/native-60-20261004-173952`, and
`out/timing/native-240-20261004-174240`. The aborted early menu-only matrix is
excluded from the gameplay results.

### Remaining work and regression coverage

Before promoting this mode, check vehicle acceleration/braking, weapon cadence,
animation, mission timers, pause/resume, save/reload, cutscene/audio sync, and a
heavy city route. Audit direct 1/30 consumers and frame-indexed work. The native
getter ignores the fixed branch's scale at `0x82C63578`, so slow-motion and other
time-scale behavior also remain unverified. Persistent zero-delta housekeeping
and logical-counter behavior require more coverage. A fixed physics step with
render interpolation may be needed if variable steps cannot preserve behavior.

`native_timing_test.py` executes 15 actual generated timer/getter routines with
deterministic kernel-clock data, including residual accounting, min/max limits,
zero skips, wraparound, large gaps, and register preservation. Separate native
hook tests cover bounded retries, callsite guards, scale handling, pause, and
logical deadlines. Presentation tests preserve unrelated callback fields for
all four targets; pure deadline-policy tests cover stalls and cadence. The current
suite passes all 24 CTest checks. These tests establish hook mechanics, not full gameplay
correctness.

## Performance test build

Build with `./build-local.ps1 -PerformanceTest`, then double-click
`Play-Crackdown-Performance-Test.cmd`. This uses the same experimental object
files but links `out/build/win-amd64-experiments/crackdown-performance.exe`, so an
existing `crackdown.exe` session can remain open. Close the performance-test game
before rebuilding that filename.

The launcher retains saved graphics preferences and defaults to a 60 FPS target.
On first launch it copies `out/userdata-fps-experimental` (or `out/userdata` if
absent) into `out/userdata-performance-test`. Later runs retain that test profile.
Its log is `out/performance-test-game.log`. Optional arguments:

```powershell
./Play-Crackdown-Performance-Test.cmd -FrameRate 120 -VehicleLod1Distance 15
```

| Flag | Behavior in this launcher |
|---|---|
| `native_frame_rate=60` | Elapsed-time gameplay; also accepts 120, 144 and 240 as pacing targets. |
| `native_discard_hitch_time=true` | Preserves intervals through 50 ms; discards older stall time. |
| `pace_gpu_wait=true` | Adds a bounded host wait to the profiled graphics progress loop. |
| `fix_guest_event_clear=true` | Synchronizes the stream worker's native events with guest clears; effective mode is fixed for the process lifetime. |
| `vehicle_lod1_distance=0` | Retains the game's mesh distances. Try 15 or 20 meters to trade vehicle detail for rendering cost. |

All hooks remain excluded from the standard build. GPU wait pacing, event
synchronization and vehicle distance overrides are disabled unless requested.
The hitch policy operates only inside the audited native timer path.

### Hitch recovery

The original capped timer retains excess elapsed time after a stall. A controlled
250 ms suspension of an owned test instance's main thread produced three
consecutive 100 ms simulation steps, matching the reported burst of movement.
The new policy retains one step of at most 50 ms, records discarded time in the
original accounting fields, and moves the committed wall-clock cursor forward.
The same injected pause then produced 50, 17, 17, 16, 16 ms updates with no
remaining debt. Normal 16/17 and 33/34 ms updates keep their full elapsed time.

This deliberately loses simulation time during severe stalls. It does not remove
the stall or the single larger recovery step, and does not add physics substeps.
Set `--native_discard_hitch_time=false` at launch for an original-policy comparison.
The trace now includes residual and discarded milliseconds. Logical 30 Hz frame
bookkeeping also resets its fractional phase when the game independently resets
its frame counter, including paused transitions, while retaining normal wrap.

### Graphics polling and event synchronization

Four of five main-thread stack samples in the garage landed in the graphics
progress path `826BBEE0 -> 826BB4F0 -> 826BCA98`. The helper's Xenon delay hints
are emitted as comments by code generation. The optional wrapper checks the
original pending-progress condition and, after 256 unsuccessful polls, waits
50 microseconds using a Windows high-resolution waitable timer. It preserves the
original timeout path, return value and guest registers.

With event synchronization held enabled, the same stationary 1440p garage view
gave the following six-second samples. Original AA, 4x filtering, lighting fix
enabled, 60 FPS target; timing tracing enabled equally in each sample:

| GPU polling | Guest FPS | Main-thread core use | Total core use |
|---|---:|---:|---:|
| Backoff enabled | 55.16 | 0.18 | 1.59 |
| Original polling | 55.00 | 0.97 | 2.38 |
| Backoff enabled again | 53.00 | 0.18 | 1.61 |

This demonstrates reduced CPU spinning, without an established throughput gain.
The graphics command thread still used approximately one core. These are
exploratory submission measurements: another user game remained open, and this
is not a controlled whole-system or heavy-city benchmark.

The separate stream worker clears guest event headers through `82A4FA70`, but
the SDK's initialized native event may remain signaled. The fix clears the
native event only at four audited worker call sites, preserving the original
guest store. Tests exercise real SDK manual- and auto-reset events, caller and
initialization guards, and both cold-start modes. Disabling the prototype live
caused a null stream-object query after several seconds; stale completion
signals can re-mark a freed stream as completed. This is the likely mechanism,
not a debugger-confirmed complete causal trace. The effective option is now
immutable after first use; compare separate launches rather than live toggles.

A fresh-launch comparison in the initial garage view held GPU backoff enabled,
disabled timing tracing, and used the same 25 m vehicle threshold. Guest FPS was
57.16–58.00 with synchronization off and about 60.50 with it on. Total CPU use
was 2.49–2.54 cores versus 1.46–1.53. The off-mode worker alone consumed
0.97–0.99 core; its stacks repeatedly entered `8225C538` through
`KeWaitForMultipleObjects`. The CPU reduction supports correcting the stale
native signal; the small FPS change remains subject to background-load noise.
The enabled build also remained responsive through repeated pistol fire,
reload, jump and walking input. Its Windows audio-session meter was nonzero in
all 120 samples over 12 seconds after firing (peak 0.706). That process was
muted in Windows, so this verifies continued audio production rather than
audible quality or synchronization.

### Vehicle mesh distance

`vehicle_lod1_distance` uses TU0's shipped `SetVehicleLod1DistOveride` setting.
The profiled game's override was already enabled at 25 meters. Lowering it changes
only the first vehicle mesh transition through the original selector `823B6518`;
later mesh thresholds, fade behavior, missing-mesh fallback and population stay
under the game's control. The selector measures distance to the transformed
bounding box, rather than the vehicle center. Values from 0 through 100 are
accepted; 0 preserves the existing setting, and the override caps the native
first threshold rather than extending it beyond that threshold.

Two stationary garage comparisons changed only the native distance setting,
restoring it afterward. Each cell is a six-second guest-submission sample:

| Comparison | Original 25 m | 15 m | Restored 25 m |
|---|---:|---:|---:|
| First | 53.66 FPS | 58.50 FPS | 53.83 FPS |
| Repeat | 55.00 FPS | 60.16 FPS | 55.50 FPS |

Read-only inspection of the renderer's selected mesh indices confirmed that
two full-detail vehicles entered their first blend and three blended vehicles
became mesh 1 only. This supports the measured direction of change, but neither
these short garage samples nor their approximately 8–9% difference establish a
city-route gain. The default remains unchanged until the visual tradeoff has
been evaluated in traffic. No pedestrian distance option is exposed yet.
`CrowdCarsFarCull` removes objects and changes population behavior; it is not a
substitute for render-only mesh control. Other named crowd LOD commands and
PerformanceScaler entries did not provide an audited active renderer path.

Local raw observations are under `out/performance/session-20261004-180953` and
`out/performance/fixed-20261004-183818`; fresh-launch event comparisons are in
`out/performance/cold-false-true-20261004-184700` and
`out/performance/cold-true-true-20261004-185230`. The latter also verified that
the compiled vehicle hook installs the requested 15 m value at startup; its
event-comparison samples temporarily restored 25 m. Menu/movie-only samples
are explicitly excluded. These logs, captures, generated guest
code and game assets are excluded from Git.

### Vehicle seat callback registration

The prior 240 FPS session's log ended with an unregistered call to `821A0C70`.
This is a valid `RetVehicleSeatID` callback: binding routine `822B5C68` stores
its address as a member-function pointer, and wrapper `8250E278` calls it
indirectly. Code generation had discovered only its internal tail at `821A0C84`.
The manifest now explicitly seeds the real entry, producing the complete
function and a matching dispatch-table registration. This correction applies
when regenerating either the regular or experimental build.

The registration regression executes the actual regenerated function through
the guest lookup table, checking its component byte and original null-path
behavior, plus register preservation. The exact city action preceding the
reported log failure has not been replayed.

## Renderer CPU investigation

The October 4 live city/ground comparison identified visibility-dependent CPU
work in the SDK graphics command thread. At the same player position,
20-second samples measured 31.55 guest FPS looking over the city and 112.21 FPS
looking down. Device-wide GPU utilization was 32–38% and 95–99%, respectively.
The command thread consumed approximately one core in both views. A subsequent
37.10 FPS street capture used a different position and is excluded from the
controlled camera comparison.

Instruction samples attributed about 43% of busy-view graphics-thread CPU to
TYPE0 register submission and metadata lookup. The SDK already batches these
constant updates for other packet types. The separate
[renderer patch and build instructions](../sdk-patches/rexglue-0.10.0/README.md)
route contiguous TYPE0 packets through that existing path while retaining
scalar side effects. Neither game rendering settings nor the simulation change.
Both D3D12 and Vulkan method regression suites pass 8,814 cases each.

A native D3D12 check loaded Campaign Solo through the district, supply point
and loadout screens into The Keep. Captures of both variants show the same
scene without an observed visual regression. Three 20-second samples used the
same player coordinates `(1894.5134, 10.9191, -1821.8202)` and camera coordinates
`(1891.5557, 12.7669, -1825.6058)` with 1440p, extreme FXAA, 4x filtering,
lighting fix, a 240 FPS target and 15 m vehicle LOD. Only one test game was
active during each measurement; its output window was 960x540 and internal
rendering remained 2560x1440. Test audio was muted and input was automated.

| Renderer | Guest FPS | Total CPU cores |
|---|---:|---:|
| Batched TYPE0 | 92.59 | 1.67 |
| Matching original TYPE0 build | 62.95 | 1.48 |
| Original TYPE0, repeat in same process | 61.35 | 1.36 |

This is an exploratory 47–51% increase at the Agency supply point, with no
visual setting reduction. The patched sample came first, followed by one
baseline launch and its repeat; a second patched launch and heavy-city
comparison remain pending. GPU utilization in the patched sample reached
89–96%, so further gains there may face a different limit. The game continued
rendering throughout each sample, and the hitch-discard counter did not grow.
These results do not establish sustained city performance or long-session
stability. The 61–63 FPS baseline is measured with a 240 FPS target, not a
60 FPS limiter. Local artifacts are under `out/performance/live-25408` and
`out/performance/live-50692`; corresponding launcher records and captures are
in `renderer-batched-20261004-193848` and `renderer-baseline-20261004-194630`.

Subsequent hands-on city testing on October 4 reported 68.0 average FPS after
an extended play session, considerable improvement across the board, and rare
dips below roughly 55 FPS. The running instance used the batched plugin with
the same 1440p/extreme FXAA/4x filtering/lighting-fix settings, 240 FPS target
and 15 m vehicle threshold. This is a player-reported overlay result, not a
recorded percentile, measured minimum, or controlled route comparison. It adds
city-play evidence to the Agency measurement; exact duration and a full
frame-time trace were not collected. Further optimization should compare
against this batched build, with particular attention to the remaining dips.

### Follow-up CPU paths and GPU instrumentation (October 5)

Two additional SDK patches remove a temporary allocation for each single-range
shared-memory request and use local bit scans when gathering shader constants.
The allocation path still performs the same locked page scan, residency checks
and uploads. Constant packing retains the same bytes, order and dirty flags.
Together with TYPE0 batching, the three fixtures pass 49,346 comparisons.

The `optimized` variant was compared against `batched` at the same Agency
position/camera and saved settings listed above, using copies of the latest
renderer-test save. Four 20-second observations used an independent frame
polling process, with CPU/guest-state and GPU sampling in separate workers:

| Order | Variant | Guest FPS |
|---|---|---:|
| A | Batched TYPE0 | 93.45 |
| B | Batched + ranges + constant packing | 96.95 |
| B, same-process repeat | Batched + ranges + constant packing | 97.30 |
| A, fresh-launch return | Batched TYPE0 | 91.90 |

This is evidence of a small gain in this fixed view (roughly 3–6%), with no
observed visual regression. It does not establish a city-route gain or separate
the contribution of the two patches. Each observed frame increment was captured
individually, player/camera coordinates remained fixed, and the hitch-discard
counter did not increase within any sample. Raw artifacts are under
`out/performance/live-46800/keep-reference-02`,
`live-37532/keep-combined-{01,02}` and `live-21816/keep-reference-return`.
An earlier legacy-sampler reference measured 93.71 FPS; its delayed polling
windows are not used to make frame-time claims.

An eight-second instruction sample from the first batched Agency run collected
521 graphics-thread observations. `UpdateBindings` accounted for about 4.4%
and `SharedMemory::RequestRanges` 1.5%; the remaining register-write family was
roughly 18%. These are exclusive sampled costs in the Agency view, and cannot
be directly compared to the prior 43% register share from a different busy
city view. A separate hot thread was the host DXGI vblank worker, not a guest
event worker; CPU totals include that host thread.

`Play-Crackdown-Renderer-Optimized.cmd` selects both new CPU changes. The
existing renderer-test launcher still defaults to TYPE0 batching alone.
The `gpu` and `optimized-gpu` variants add optional, sampled GPU timestamps;
see [build and profiling instructions](../sdk-patches/rexglue-0.10.0/README.md#gpu-measurements).

### GPU costs at the fixed Agency view

The timestamp variant was exercised on the RTX 5070 Ti with the same camera,
240 FPS native target, 15 m vehicle threshold, lighting fix/FAST readback,
bloom, shadows and 4x filtering. The output window stayed at 960x540; renderer
captures independently confirmed internal dimensions of 2560x1440 and
1280x720. Each observation lasted 20 seconds and sampled GPU timestamps every
60 guest frames. The profiler's actual-helper fixture passes 2,458 checks,
and the CSV completeness/aggregation suite passes 10 tests.

| Settings and timing mode | Guest FPS | Mean guest GPU ms/frame | Complete GPU samples |
|---|---:|---:|---:|
| 1440p, extreme FXAA, whole submissions | 97.15 | 8.00 | 32 |
| 1440p, extreme FXAA, detailed categories | 95.55 | 8.16 | 32 |
| 720p, extreme FXAA, whole submissions | 98.00 | 3.94 | 32 |
| 720p, extreme FXAA, detailed categories | 98.20 | 4.08 | 32 |
| 1440p, FXAA disabled, whole submissions | 98.05 | 7.93 | 32 |
| 1440p, extreme FXAA, whole submissions, return | 98.20 | 7.99 | 33 |

The whole-only 1440p result is close to the uninstrumented optimized samples
(96.95–97.30 FPS). Detailed timestamps may affect pipelining; the detailed
1440p run also recorded 3.3% more guest draws than the whole-only run, so the
difference cannot be assigned entirely to instrumentation. These are separate
launches at the same fixed camera, with variable ambient world activity.

| Inclusive GPU category | 1440p mean ms | 720p mean ms |
|---|---:|---:|
| Render-target updates/transfers | 2.849 | 0.677 |
| Resolves, including readback GPU work | 1.648 | 1.003 |
| Readback GPU work (overlaps resolves) | 0.876 | 0.746 |
| Texture conversion/loading | 0.682 | 0.297 |
| Shared-memory uploads | 0.546 | 0.537 |
| Gamma | 0.025 | 0.008 |
| Extreme FXAA | 0.054 | 0.016 |

These overlapping categories must not be summed as a frame total. Scopes start
at their first actual GPU work and omit leading setup/barriers. The total is
the sum of completed guest command-list intervals; it excludes the presenter's
separate command list, host processing/waits and physical presentation. All
six sample windows contain complete frames without invalid results, skipped
submissions, query-budget drops or truncated detail scopes.

The GPU work roughly halves at 720p, while throughput increases by less than
1%. The graphics command thread consumes approximately 0.95 CPU cores in both
whole-only captures. Together with the instruction-pointer evidence, this
points to CPU command processing as the main limit in this view. Lowering
resolution alone is not an effective FPS improvement here. It does not rule
out a GPU limit in other views or at higher frame rates.

Turning FXAA off saves only about 0.06–0.07 ms of GPU time relative to the two
1440p whole-only controls, consistent with the measured FXAA scope. The return
run with extreme FXAA is slightly faster in aggregate FPS than the off run;
there is no meaningful throughput gain from disabling it in this comparison.

The renderer records roughly 4,100–4,300 guest draws, 41 resolves, 48 texture
loads, 15–16 MB of uploads and three command submissions per sampled frame.
Render-target transfers scale by about four times with four times as many
pixels; uploads and much of the readback path scale less. Final FXAA/gamma
are small in comparison. This favors further CPU state/constant/upload work
for immediate throughput, with render-target transfer reduction as a GPU
headroom investigation.

Source inspection found that `direct_host_resolve` still goes through the
existing render-target dump and subsequent resolve dispatch. It is not a
ready-made fused resolve path. A real fast path would need to preserve guest
tiling, endian conversion, MSAA selection, scaling and memory visibility, with
a fallback for unsupported cases. GPU timing does not justify simply dropping
lighting readbacks or changing rendering quality.

Local sample directories are `out/performance/live-51052/whole1440-01`,
`live-52416/detail1440-01`, `live-40296/whole720-01` and
`live-50736/detail720-01`, `live-51384/whole1440-noaa-01` and
`live-45560/whole1440-return`; corresponding `renderer-optimized-gpu-*` directories
contain the full GPU CSV, launch arguments, logs and captured output. These
fixed-view results do not establish performance in a busy city route.

### Further CPU command processing (October 5)

Two more patches extend the optimized renderer. Contained control-register
packets now avoid per-word virtual dispatch and metadata lookup, while retaining
ordered volatile stores and the original diagnostic path at debug verbosity.
Deferred command recording retains its constructed storage across submissions
and resets a used-length counter. Every replayed field is explicitly initialized;
the unused tail is never replayed. Graphics settings, draw calls, readbacks and
simulation timing are unchanged.

The same fixed Agency position/camera was measured with 20-second observations,
copies of the same save, a 240 FPS target, 15 m vehicle threshold, 1440p internal
rendering, extreme FXAA, lighting fix, bloom, shadows and 4x filtering. The output
window remained 960x540. Builds and debugger sampling were outside measurement
windows. Each frame increment was observed individually, player/camera positions
matched, and the hitch-discard counter did not grow in any sample.

| Order | Variant | Guest submission FPS |
|---|---|---:|
| A, two samples | Earlier `optimized` | 91.65 / 91.65 |
| B, two samples | `stream`: command-storage reuse only | 97.90 / 95.50 |
| C, two samples | `frontend`: control batching and storage reuse | 111.10 / 108.75 |
| A, fresh-launch return | Earlier `optimized` | 97.65 |
| D, two samples | `reuse`: shader-constant reuse only | 103.10 / 98.30 |
| E, two consecutive samples | `frontend-reuse`: all candidates | 112.15 / 111.95 |
| C, fresh-launch return, two consecutive samples | `frontend` | 111.90 / 112.15 |

The fresh reference return exposes meaningful run variation. Relative to that
stronger reference, the combined change gains about **11–15%** in this view.
Command-storage reuse alone overlaps the reference range, so these observations
do not establish its separate FPS contribution. Control batching accounts for
the clear additional gain in the combined comparison. These are stationary
submission rates, not monitor presentation rates or busy-city minimums.
Captured output was visually consistent; broader city/campaign play remains
necessary. Existing resolve-sample warnings occur in both old and new logs.

Shader-constant reuse compares and copies each value once, retaining a clean
constant buffer when its bits are unchanged. It initially measured 103.10 FPS
in isolation, but its repeat was 98.30 FPS. More decisively, the full combination
and a fresh `frontend` return both measured approximately 112 FPS using identical
automatic startup and sampling sequences. This does not establish an additional
FPS gain, so constant reuse remains an explicit experimental variant. Changing
values add comparison work; passing correctness tests does not establish a win.
The default `frontend` renderer also passed a short camera/movement/jump/two-shot
smoke test after timing, with continued rendering and the expected ammunition
change. That smoke test is not a city benchmark or campaign stability test.

Subsequent hands-on testing of this build reported 90–100 FPS in most areas,
dips into the 60s in busier scenes, and a lowest observed rate of approximately
50 FPS in the same crowded areas that previously slowed down substantially.
This provides city-play evidence beyond the fixed Agency view. The figures
are player observations, not a recorded minimum, percentile or controlled
route comparison; no session duration was specified.

`Play-Crackdown-Renderer-Optimized.cmd` now selects `frontend`. The earlier
three-patch renderer remains available with
`Play-Crackdown-Renderer-Test.cmd -Variant optimized`. Control-only and
storage-only variants are also supported by the builder. The combined build
passes 33,516 control-register comparisons, 732,832 deferred record/replay checks,
and the prior 49,346 register/range/constant comparisons. Regression fixtures
check real SDK methods, including poisoned retained storage and exact logger
payloads. Build-generated offsets and DLL hashes identify each sampled binary.

A fresh 400-sample graphics-thread profile of the earlier optimized build found
28 observations in base register writes, 16 in metadata lookup and 12 in D3D12
scalar writes. It also resolved the previously unattributed CRT cost: 58 samples
(14.5%) were copies in `IssueCopy_ReadbackResolvePath`, 10 were shared-memory
uploads and four were constant packing. Only two samples were command-storage
`memset`. These are sampled shares from this scene, not additive gain forecasts.

Readback copies remain necessary under the current visibility contract. Their
destination is the direct physical mapping, which bypasses guest page-watch
faults, so this cost is not repeated write-fault handling. Local CRT disassembly
showed 49 of the 58 copy samples in an AVX-512 non-temporal loop with `sfence`;
nine were in medium-sized `rep movsb` copies. An isolated aligned/unaligned
comparison passed 704 byte/guard checks, but an AVX2 streaming replacement was
substantially slower for hot medium buffers and offered no meaningful cold-copy
gain. This was an ordinary-RAM benchmark, not a GPU-mapped benchmark. No custom
copy routine or readback omission was added.

Raw observations and launch/capture records are under
`out/performance/renderer-optimized-frames-reference-20261005-044436`,
`renderer-stream-frames-20261005-045648`,
`renderer-frontend-frames-20261005-050111` and
`renderer-optimized-frames-return-20261005-050425`. The first directory also
contains the separate instruction-stack sample. Synthetic copy artifacts are
under `out/readback-copy-benchmark`. Constant-reuse and return captures are in
`renderer-reuse-frames-20261005-050905`,
`renderer-frontend-reuse-frames-20261005-051454` and
`renderer-frontend-frames-return-20261005-051657` under the same performance
directory. The last directory contains the separate post-input smoke capture.
