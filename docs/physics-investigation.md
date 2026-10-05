# Intermittent movement bursts at high frame rates

Status: investigating, 2026-10-05. A step-down correction passed human testing
and is enabled by default in native timing builds. Original timing is unchanged.
The distant pedestrian movement/animation correction also passed human testing.
Distant vehicle speed and the occasional stair recurrence remain unresolved.

Hands-on testing reports occasional accelerated airborne movement, sometimes
after standing still, with no obvious vehicle impact or damage. Earlier reports
also describe excessive NPC and vehicle knockback. These may have different
causes; a player movement finding does not establish an impulse fix.
Later testing also reports several NPCs moving abnormally fast in crowded areas,
and Agency announcer lines ending prematurely. Dialogue interruption needs its
own playback/stop audit; this report does not establish a common cause.

## Live observations

The running optimized TU0 build used native timing with a 240 FPS target and
`native_discard_hitch_time=true`. The first five-minute read-only capture collected
37,500 samples. The discarded-time ledger stayed unchanged; its largest sampled
published timestep was 47 ms. In the initially marked window, simulation elapsed
time tracked wall time and sampled steps were at most 15 ms. The agent position
was static there, so that window cannot identify the reported airborne event.
Chat markers represent receipt time, with uncertain delay.

A second capture added the player proxy, camera, cumulative process CPU time,
time scales, and Havok's internal clock/step. It observed two short movement
bursts around capture seconds 83.4 and 170.0. Displacement over roughly 200 ms
windows corresponds to approximately 78–83 world units/sec, compared with
ordinary movement around 11–13 in that capture. Both the proxy and camera followed
the agent through the displacement. These are sampled speeds, not velocities
read from the controller. One burst falls within the broad window of the user's
delayed event report; exact correspondence remains uncertain.

Published steps around these bursts stayed approximately 7–15 ms. Both time
scale globals were 1.0 throughout the second capture. Havok's clock periodically
shifted from roughly 63 seconds back to 33 seconds, about every 30 seconds;
neither burst coincided with that rebase. This gives no evidence that the bursts
are caused by the earlier retained hitch-time debt or directly by a clock rebase.
It does not exclude a character/controller timing error.

A third, fuller controller capture observed intended horizontal movement near
12–13 units/sec while the controller's derived and agent velocities climbed past
100 and briefly exceeded 200. Positions and the phantom followed the accelerated
movement. Published and stored Havok steps around these bursts remained 6–13 ms;
the support-entity pointer was zero. This localizes the observed divergence to
the character/controller movement path, without yet identifying which operation
first introduces it. It does not prove that a smaller delta is the cause.

An opt-in controller entry/exit trace subsequently captured a player burst whose
derived speed peaked near 102 units/sec while intended speed remained near 13.2.
At its onset, horizontal derived velocity grew over successive updates from
approximately `(12, 13)` to `(31, 31)` and then `(70, 66)`. Published steps were
6–10 ms and the discarded-time ledger did not change in this window. Controller
flags repeatedly contained `40ae00`; the local routine sets the additional bits
after a successful step-down adjustment. This is evidence of controller feedback,
but does not yet identify whether that adjustment or a subsequent solver stage
introduces the excessive movement.

NPC proxy-controller calls also produced brief derived-speed spikes, including
roughly 57 and 71 units/sec with intended speed near 1.25. Some returned to normal
on the following update. A one-update proxy correction does not establish the
same cause as the player's sustained burst; intermediate phantom/controller
positions are needed to distinguish correction from actual continued movement.

The expanded stage trace captured the player accelerating while descending
steps, confirmed independently by the tester. A successful step-down call is
followed by a downward correction entering the local solve. That solve produces
extra horizontal displacement, which the velocity-update stage converts back
to horizontal velocity for the next update. Requested movement stays near
13 units/sec; repeated traversal later produced horizontal derived speed near
180. The trace localizes the feedback sequence. Human comparison subsequently
reported greatly improved steps and normal jumps and curbs with the correction
enabled. This validates the observed player traversal improvement, not every
physics or animation path.

Some captured NPC spikes were one-update downward ground corrections, with
ordinary horizontal movement immediately afterward. These do not explain every
reported zooming NPC. The tester also reports accelerated NPC animations, so
NPC animation/update timing remains a separate open audit. The same test also
reported occasional zooming distant cars, which this character-only correction
does not address.

Local evidence is under `out/performance/physics-capture-20261005-13216`,
`physics-capture-expanded-20261005-13216`, and
`physics-capture-velocity-20261005-13216`. The fuller controller capture is
`physics-controller-20261005-13216`. Captures and extracted game instructions
are ignored local artifacts.
The entry/exit capture is `physics-trace-20261005-213952-335`.
The expanded stage capture is `physics-trace-20261005-215035-842`.

## Relevant TU0 paths

- `sub_8248F210` passes the published milliseconds, converted to seconds, through
  `sub_82492118` to the Havok world. The observed simulation virtual function is
  `sub_829BD048`; its stored step is at simulation object offset 8. World offsets
  16, 20 and 24 hold current time and integration interval boundaries.
- `sub_8299ECC8` conditionally invokes `sub_8299EA88` to shift world and body
  timestamps. Rebasing is an existing game path, not added by native timing.
- `sub_822B97B0` dispatches character movement through an embedded component at
  agent offset 1344. `sub_822AB708` and `sub_822A9E78` are two controller paths.
- The controller converts position differences back into velocity using division
  by the incoming timestep. For example, `sub_822AB708` obtains the phantom's
  position and divides its difference from the prior controller position by `f31`,
  the incoming delta. The result is stored at component offset 352, then copied
  into other velocity/history fields.

That last conversion is a candidate amplification point: the same positional
correction divided by a smaller delta yields a larger velocity. This is a
hypothesis, not proof that the conversion is wrong. We need the intended
velocity, derived velocity, support entity and positions around the first
divergence before changing it.

## Repeatable capture

Load into gameplay, obtain the game PID, then run from the repository root:

```powershell
python tools/sample-physics.py capture --pid <PID> --duration 900 --output out/performance/physics-session-01
```

Use a new output directory each time. The default metadata is the normal
launcher build's `offsets.json`; `--metadata` selects a different matching build.
The tool verifies loaded runtime/renderer DLL hashes and known guest virtual
functions before interpreting the relevant layouts. It opens the game for
read/query access only. It does not inject input, invoke guest functions, suspend
threads, or write game memory.

The CSV includes SDK submission counters, cumulative process CPU time, published
timing, Havok timing, agent/proxy/camera/phantom positions, controller velocity
fields, support entity and controller flags. Its default sampling period is
8 ms. A 20-second live smoke capture produced 2,500 samples with no errors;
median read duration was approximately 0.15 ms on the development machine.
Read duration is observer cost, not a measurement of game slowdown.

Mark a reported event in another terminal, including a lookback for late reports:

```powershell
python tools/sample-physics.py mark --output out/performance/physics-session-01 --ago 120 --uncertainty 60 --note "Airborne burst, no apparent damage"
```

Markers append to `event-markers.jsonl`; they can be added after capture ends.
`--ago` and `--uncertainty` are seconds. Closing the game or interrupting the
observer ends observation and preserves flushed data.

These reads are asynchronous. Stability flags reject some overlapping updates,
but do not provide an atomic snapshot or prove every update was observed. CPU
counter differences measure process CPU consumption, not the in-game graph's
individual subsystem timings.

## Next checks

Capture a burst with the controller stage hooks and locate whether intended
velocity, step-up/down adjustment, collision/phantom correction, or derived
velocity changes first. Compare the first divergent stage against a normal
update before modifying the movement path.

Compare the same controller path with native timing at 60 and 120 FPS and the
original 30 FPS timestep, keeping the optimized renderer and profile consistent.
Original timing is a control for this recompilation; it cannot by itself prove
that the original console game has the same bug. Avoid applying a global speed
clamp or changing timestep policy before identifying the failing conversion.

## Controller entry/exit diagnostic build

The experimental build now includes an opt-in `physics_trace_path` flag. With
an empty path it forwards each original routine without guest reads or writes.
With tracing enabled it records actual incoming deltas and before/after state at
`sub_822B97B0`, `sub_822AB708`, `sub_822A9E78`, and `sub_829BD048`. All original
routines still receive the original context exactly once.

Thirteen additional component hooks split the character update into preparation,
step-up/down, collision solve, and contact/support/velocity/vertical/water stages.
The CSV records agent, controller and phantom positions, intended and derived
velocity, flags, support pointers, return register `r3`, and incoming deltas.
The labels describe stages inferred from the TU0 callers and instructions; they
are not recovered source symbols. Child rows are emitted before their parent's
row, so sort by entry timestamp when reconstructing call order.

The local player and high-speed character calls are recorded on every invocation;
ordinary NPC calls are sampled per actor/caller at a 100 ms simulation cadence.
Havok step calls are recorded individually. A bounded queue feeds a background
CSV writer; guest threads use a nonblocking queue lock. Drops are counted in the
CSV. Controller duration excludes the hook's surrounding work, but parent
durations include observation inside nested hooked calls. Diagnostic tracing
still adds CPU work, so its frame rate is
not a clean performance benchmark. Shutdown can lose the final buffered batch.

Build and launch from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build-physics-trace.ps1 -Jobs 2
.\Play-Crackdown-Physics-Trace.cmd
```

This stages a separate executable with the optimized frontend renderer. The
launcher seeds `out/userdata-physics-trace` once from the existing profile,
excluding its shader cache. Later diagnostic progress stays in that separate
profile. Each launch writes its arguments and controller trace to a new directory
under `out/performance/physics-trace-*`. The regular executable is not replaced.

The default target is 240 FPS. Use `-FrameRate 60`, `-FrameRate 120`, or
`-FrameRate 0` for comparisons; zero selects original game timing. Normal launches
use the physical controller. `-Automation` is only for tool-driven tests and
replaces physical input with the existing automation driver.

The 28-test regression suite passed, including separate forwarding tests with
tracing disabled, enabled, and an unavailable output path. These compare complete
returned PPC contexts and guest fixture memory against original dispatch for all
17 hooks, and verify the phantom-position pointer chain. Human gameplay recorded
Havok, player and crowded-area NPC calls with physical controller input. The
expanded stage trace captured player bursts on descending steps.

## Native timing step-down correction

`sub_822B4D40` returns a downward velocity calculated as
`-height * 0.4 / dt`. Repeating a 40% positional correction per update changes
its response when native timing raises the update rate. The
`normalize_character_step_down` flag changes that fraction to
`1 - pow(0.6, dt * 30)` for updates shorter than 1/30 second. This preserves the
original correction at 30 Hz and gives the same elapsed-time response across
60/120/144/240 Hz in an isolated correction model. It is a candidate fix, not
proof that the full collision/controller system is now frame-rate independent.

The wrapper changes only the successful negative vertical output at the audited
local-controller call site `0x822AA884`. Incoming timestep, collision queries,
horizontal outputs and returned registers remain as the original produces them.
Original timing, unrelated callers, unsuccessful corrections, invalid deltas
and longer updates pass through. It does not change the NPC proxy controller or
NPC animation timing. The flag defaults to true for native timing builds;
`--normalize_character_step_down=false` provides a comparison control. The
baseline diagnostic launcher sets false explicitly and the correction launcher
sets true explicitly, preventing persisted settings from changing the comparison.

Build the separate comparison variant and launch after saving and closing the
baseline diagnostic game; both diagnostic launchers use the same save profile:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build-physics-trace.ps1 -StepDownVariant -Jobs 2
.\Play-Crackdown-Physics-Step-Down.cmd
```

Compare the same descending steps, curbs and slopes, then check jumping and
ordinary movement. Both variants retain stage tracing. The correction tests
verify frame-rate-independent response in the isolated model, varying deltas,
all opt-in/caller/result guards, unchanged original input and returned contexts,
and the exact allowed output-memory change. Human gameplay comparison reported
greatly improved steps and normal jumps/curbs. Continued testing is still needed
for other terrain, impulses and movement modes.

A later session included one possible recurrence on a staircase that was not
retested. Keep that unresolved rather than treating the controller as fully
corrected. The neighboring step-up routine `822B4100`, called from the same
parent at return address `822AA820`, also references 0.4, but inspection shows
it is a comparison threshold rather than the step-down correction multiplier.
That constant alone does not justify the same scaling. Capture the repeated
staircase with the existing controller-stage trace before changing this path.

## Distant NPC follow-up

The user reports that distant NPC movement and animation still speed up, while
nearby NPCs appear normal; distant cars may also be affected. A separate crowd
subsystem uses dedicated low-detail models and fixed walk/run movement steps,
plus a rounded `0.0333333` animation phase increment. Its live pool is distinct
from the nearby character controller hooks above. The [full fixed timing audit](fixed-timing-audit.md) inventories this
path, other timer/response candidates, and the limits of read-only pool samples.

## Native crowd correction test

The crowd correction now scales consumed movement and animation by published
elapsed simulation time, retains fractional reference progress through path
countdowns, and normalizes steering's per-update response. It preserves the
authored walk/run vectors and the original path, clip and rendering routines.
The flag `normalize_crowd_timing` defaults to true only when native timing is
active; `--normalize_crowd_timing=false` provides a comparison control.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build-physics-trace.ps1 -CrowdVariant -Jobs 2
.\Play-Crackdown-Crowd-Timing.cmd
```

This stages `out/variants/physics-crowd/crackdown-physics-trace.exe`, retains the
optimized renderer and step-down correction, and uses the existing diagnostic
save profile. Save and close another diagnostic instance first. The physical
controller remains active. Baseline and step-down-only launchers explicitly
disable the crowd correction so persisted settings cannot change the comparison.

All 30 regression tests pass. The crowd fixture executes actual generated
movement/animation code at 30/60/120/144/240 Hz, including LOD3/4/5 clip paths,
variable deltas, segment ends, slot reuse and disabled-hook equivalence. Human testing confirms that pedestrian movement and animation look normal.
Read-only live samples agree: movement and animation track published elapsed
time at approximately 1.00x, versus approximately 2.22x before correction.
Background cars still appear too fast until they begin rendering. The sampled
pedestrian pool contains model IDs 0 through 5; its selected and unselected
objects both show normalized motion. This does not establish that civilian
vehicle driving shares that path. The separate `cTrafficDrivingAgent` constructor
(`821ABF30`, vtable `8207EB48`) and its state dispatcher (`821AC810`, reached through `821AFF40` /
`821AFE40`) are follow-up
leads. Fixed 1/30 values also occur in `821A48A8` and `821AB8A0`; their neighboring
comparisons suggest driving-distance thresholds, so they need semantic review
before changing them. The independent Agency voice-line timer investigation remains
unresolved; this change does not establish a connection to those cutoffs.
