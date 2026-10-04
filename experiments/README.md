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
  own original 2000 ms discard behavior, retained by the hook.
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
This points toward CPU work rather than a universal 60 Hz presentation cap,
but stack sampling is needed to identify the actual expensive routines.
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
all four targets; pure deadline-policy tests cover stalls and cadence. All 18
CTest checks passed. These tests establish hook mechanics, not full gameplay
correctness.
