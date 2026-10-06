# TU0 fixed timing audit

The October 5 audit scanned the current 177 generated recompilation units
(41,408 functions, approximately 185 MB of generated C++) and 925 C++ source/header
files across this project's hooks and the matching frontend SDK source tree.
The scanner resolves PPC address construction against a locally decoded loaded
image, inventories scalar constant loads, selected integer immediates, adjacent
counter updates, clock references and direct calls. It does not modify the game.

Numeric matches are candidates, not a count of timing bugs. The scan found:

| Pattern | References |
| --- | ---: |
| Approximately 1/30 | 14 |
| Approximately 1/15 | 26 |
| Approximately 1/60 | 13 |
| Approximately 1/120 | 4 |
| Approximately 1/240 | 1 |
| 0.4 / 0.6 gains or retention values | 68 / 172 |
| All selected scalar constants, including gains and unit conversions | 815 |
| Selected integer immediates | 5,687 |
| Adjacent load/increment/store counters | 2,720 |
| Known clock references | 183 |

The reciprocal values occur in 52 distinct functions. Sixteen of the 1/15
references use a separate constant in packed nibble/quantization code; the
integer conversion and masking around those loads makes them poor timing leads.
Two 1/120 references are coefficients inside polynomial math routines. These
examples show why replacing every matching number would break unrelated code.

## Prioritized findings

| Path | Evidence | Status and next check |
| --- | --- | --- |
| Distant crowd movement | `82330B68`, `82342470` and `82338220` calculate object field +180 using walk speed times 1/30 or run speed times 1/15. `82331198` advances position +128 by a stored step vector +112 and decrements segment steps +176 once per call. | Native elapsed-time displacement and fractional reference-step countdown implemented. Generated-routine tests pass; human testing confirms normal crowd movement. |
| Distant crowd animation | `82330888` advances phase +228 using multiplier +184 times `0.0333333`, then wraps against clip length and evaluates the animation. | Native elapsed-time interval implemented; original wrapping and LOD3/4/5 evaluation retained. Generated-routine tests pass; human testing confirms normal crowd animation. |
| Background cars | `82333538` smooths direction by 10%, adds direction times authored step +116 and multiplier +120, and decrements segment steps +124 once per call. `82332EC8` invokes it before visibility classification. | Separate low-detail car pool confirmed. Native displacement/countdown/direction correction passes generated-routine tests; live movement is approximately 1.00x authored elapsed time versus 2.38x before. Human gameplay comparison reports good behavior. |
| Agency garage door timer | `cAgencyGarageDoor`, vtable `820C36F0`, update `8252EDA0`, adds 1/30 to field +2404. Consumer `8252F2E8` compares it with 5.0 and resets it for state zero or a player inside. | Opt-in elapsed-time correction on the object-timing experiment. Actual generated update/consumer tests verify the five-second timeout, state transitions and original resets. Human comparison pending. This timer is a door timeout, not a demonstrated speech timer. |
| Character proxy timer | `822AB708` contains a branch adding 0.05 to a component field. | Audit the branch, field role and accumulated-time consumers. A fixed value here does not alone prove that it drives movement. |
| Two related object state timers | `8259A738` and `8259AD98` add 0.05 to field +368 and compare it with another threshold. | Verify object identity and how often these states run. |
| Lift model fade | `cLift`, vtable `820C43C8`, update `82531880`, retains elapsed f1 in f31 and calls `82531228`. That routine increments/decrements visual fields +124/+128/+132/+136 by 1/15, clamping to [0,1]. | Opt-in elapsed-time correction on the object-timing experiment. Actual generated fade tests preserve the half-second reference duration and transform calls. Human comparison pending; the garage smoke route did not invoke a lift with an attached visual. |
| Input/movement response | `8228B0F0` applies a 1/30 proportional decay to field +6980 each call; `822A2C90` has another fixed-step response. | Inspect whether these are response coefficients, deadlines or calibrated geometry. Normalize only after identifying their elapsed-time behavior. |
| Entity displacement/impulse candidate | `82354560` multiplies a scalar and a direction vector by 1/60 before virtual dispatch. | Identify the callback and receiving setter. An event invoked once per impact needs a different treatment from an update invoked every frame. |
| Shared dt getter | `8254C568` has 73 direct caller functions and selects fixed or published clock-derived timing. | Existing native clock tests exercise the elapsed-time branch. Audit callers that assume the original reference interval separately. |
| Player step-down gain | `822B4D40` contains the 0.4 correction gain. | Human-tested elapsed-time correction is enabled by default in native timing. See [physics investigation](physics-investigation.md). |
| Frame-count conversions | `82265C70` converts a frame count with 1/30; logical engine deadlines also use frame indices. | Preserve the meaning of authored frame-count data. Changing its conversion is not equivalent to changing simulation dt. |

The distant pedestrian crowd uses dedicated LOD4 walk/run clips. Its pool has
a signature of `0xACED0FF0`, 1,500 slots of 672 bytes,
starting at `0x82E64090`. This is separate from the nearby Havok character
controller traced for the player's step-down correction. It fits the user's
distance-dependent observation. Stable-path samples before the correction showed
animation advancing about 1.85 to 2.22 times published elapsed simulation time.
These asynchronous samples support the static per-update finding; they do not
provide an exact invocation trace. The fixed 0.05 steering coefficient is now
normalized to preserve its response over elapsed time as well.

## Crowd correction

`src/crowd_timing.cpp` uses ReXGlue instruction hooks at `823311C8` / `823311D8`
for movement and segment countdown, `823308E0` for the animation interval, and
`823383AC` for steering. It consumes published retained milliseconds at
`82D99128`, expressed as reference updates (`milliseconds * 30 / 1000`). Authored
walk/run vectors, including the run multiplier, remain unchanged in memory.
Movement scales only the consumed XYZ displacement; W stays untouched.
Fractional reference updates are kept in host state per pool slot and reset on
allocation, path/count rewrites or clock resets. Movement caps at the remaining
segment; original path initialization runs when its countdown reaches zero.
Animation keeps the original wrapping, clip selection and evaluation routines.
Steering uses `1 - pow(0.95, reference_updates)` in place of the per-call 0.05.

The correction is active only with native timing and
`--normalize_crowd_timing=true` (the default). Original timing, explicit opt-out,
invalid clock samples and non-pool objects preserve original behavior. The
separate `Play-Crackdown-Crowd-Timing.cmd` launcher enables it together with the
existing step-down correction; baseline diagnostic launchers explicitly disable
crowd normalization. The running standard executable is not replaced by staging
this diagnostic build. See [physics investigation](physics-investigation.md).

The integration fixture executes locally generated TU0 movement and animation
routines at 30/60/120/144/240 Hz, with variable millisecond deltas. It checks
reference-rate displacement/countdown, animation phase in LOD3/4/5, clip wrapping,
slot/path/count resets, segment boundaries, steering response and unchanged W.
Pass-through comparisons include complete PPC contexts, object/stack memory and
the crowd pool's animation evaluation registry. Human testing confirmed that pedestrian movement and animation look normal.
A 20-second live capture recorded median elapsed-time ratios of 0.999999 for
animation (17,085 stable pairs) and 1.000040 for projected displacement
(15,105 pairs), versus about 2.222 before the fix. These are filtered asynchronous
samples, not exact update traces. A broader capture also found approximately
1.00 movement ratios for unselected pool objects. The user still reports fast
background cars; their separate pool is described below.

## Background car correction

`82331E60` loads LOD4/LOD5 civilian saloon and box-van assets into a separate
car subsystem. Its manager is `82FDE4B0`, signature `0xACED0FF0` is at manager +4,
and its 1,000 slots of 480 bytes start at `82FDF470`. `82332D78` dispatches the
car worker `82332EC8`. That worker calls movement `82333538` before deciding
whether/how to draw each car. This explains why correcting the pedestrian pool
did not fix distant cars. Visibility bits are classification data, not proof
that a particular car was submitted for drawing.

Cars retain a nominal movement step of approximately 1/3 at +116 and an authored
random multiplier at +120. The original worker adds the resulting direction
vector to position +64 once per invocation and subtracts one from countdown
+124. It also approaches target direction +16 with a fixed 0.1 gain in direction
+32. None of these operations consumes elapsed time. A 15-second read-only
capture collected 938 bulk samples and 117,250 car rows. After filtering for
stable paths, clocks and nominal vectors, 57,060 position pairs had a median
ratio of 2.3803 to displacement expected at the authored 30-update reference
rate. Pairs with visibility bits zero also measured 2.3804. These are filtered
asynchronous observations, not exact call counts or a trace of mesh transitions.

A 20-second corrected capture collected 1,250 bulk samples and 156,217 car rows.
Its 76,757 stable position pairs measured a median ratio of 0.999988, including
0.999990 for zero visibility bits and 0.999984 for bits `0xC`. Both captures use
the same filter: unchanged slot/path/authored multiplier/visibility classification,
stable clock reads, 2–50 ms of published elapsed time, nearly unchanged nominal
vectors (relative tolerance 0.002, absolute tolerance 0.00001), projected ratios
between zero and ten, and perpendicular displacement residual below 0.0004.
Slot reuse and asynchronous writes still limit inference; these samples support
straight-path timing, not a complete traffic-transition validation.

`src/crowd_timing.cpp` hooks the direction gain at `82333584`, consumed
displacement at `823335D4`, countdown store at `823335E4`, and allocation reset at
`82333388`. Movement/countdown use the same retained native milliseconds and
fractional reference-step policy as pedestrians. Direction uses
`1 - pow(0.9, reference_updates)`. The authored step, random multiplier and
nominal displacement stored at +48 remain unchanged. W remains unchanged.
Route initialization still runs through `82333458` / `82343ED8`; allocation,
route/count rewrites and clock rollback reset host fractional progress.

`--normalize_background_car_timing=true` is the default with native timing;
explicit opt-out and original timing preserve the original car routine.
`Play-Crackdown-Background-Cars.cmd` stages a separate comparison with the
pedestrian and step-down corrections enabled. Nearby physical vehicles keep
their existing driving/physics code. The earlier `cTrafficDrivingAgent` lead
(`821ABF30`, dispatcher `821AC810`) belongs to that separate path. Its fixed
1/30 comparisons in `821A48A8` / `821AB8A0` are not evidence for changing their
distance thresholds.

The car integration fixture executes the actual generated movement routine at
30/60/120/144/240 Hz and variable intervals. It checks authored speed/multipliers,
elapsed-time direction response, fractional countdown, segment boundaries,
route initialization, slot reuse, route rewrites and W. Disabled/unsupported
cases compare full PPC contexts and object/stack memory with the unhooked body.
Human testing of this diagnostic build reported good behavior. Continued play
should include straight cruising, turns and the transition to nearby traffic.
Normalizing direction response does not prove identical curved trajectories at
every frame rate or validate every vehicle behavior.

## Experimental world object timing

The object-timing branch adds two narrow corrections, both disabled by default.
They require native timing and explicit `--normalize_lift_fade=true` or
`--normalize_garage_door_timer=true`. The consumed increments change; shared
constant memory and the original state machines remain untouched.

For `cLift`, the update retains its incoming elapsed seconds in nonvolatile f31
before calling the transform/fade routine. The wrapper verifies the class and
the known update return address, and the instruction hook at `82531310` consumes
`2 * elapsed_seconds` instead of 1/15. The 30-update reference fade takes half a
second; the uncorrected routine reaches its limit in 15 calls, or 125 ms at
120 updates per second. Original transform getter/copy/set calls, four-channel
writes and clamping remain in the generated routine.

For `cAgencyGarageDoor`, the wrapper captures incoming f1 before the base object
update can clobber that volatile register. The hook at `8252EDEC` consumes those
elapsed seconds instead of 1/30. The original timeout consumer still compares
field +2404 against 5.0, performs player proximity queries and resets the timer
in the original states. At 120 updates per second the uncorrected five-second
timer expires in about 1.25 seconds; the corrected timer expires after five
elapsed seconds, within one update's floating-point/threshold boundary.

Both hooks reject nonfinite, negative or greater-than-100-ms intervals,
unrecognized classes and changed authored constants. Native timing disabled and
explicit opt-out execute the original code. The integration fixtures execute
the actual locally generated routines at 30/60/120/144/240 Hz and variable
intervals. They compare complete PPC contexts and object/stack writes against
the unhooked routines for pass-through cases, including bit-identical 30 Hz
behavior. The door fixture also executes the real timeout consumer and state
transitions; the lift fixture retains the three original transform boundaries.
CSV tests check baseline/corrected arithmetic and the 8,192-row cap.

Build and test without replacing the normal executable:

```powershell
./tools/build-physics-trace.ps1 -ObjectTimingVariant -Jobs 4
./Play-Crackdown-Object-Timing.cmd -FrameRate 120
./Play-Crackdown-Object-Timing.cmd -FrameRate 120 -Baseline
```

Each launch makes a fresh private copy of an existing save profile, excluding
its cache. `-ProfileSource <directory>` selects the source explicitly. Logs and
optional bounded `fade.csv` / `garage-door.csv` traces are under
`out/object-timing/session-*`. `-FrameRate 0` selects original timing;
`-PrintArguments` previews without starting the game or copying profiles.
The traces include incoming elapsed seconds, before/after values and whether
the consumed increment was actually replaced. A door reset can make its final
after value zero even when the normalized increment executed. These experiments
still need a visible lift and garage door comparison before promotion to main.
An automated Agency gameplay smoke run did invoke the audited garage door:
its incoming update intervals were typically 8–9 ms at 120 FPS, confirming
seconds as the unit. Those samples remained in state zero, where the original
consumer resets the timer; they do not validate the visible timeout duration.

The SDK's UI has a 1/60 fallback only when no valid elapsed interval is available.
Its normal ImGui delta uses elapsed time. Guest vblank uses the configured video
mode refresh rate; that clock serves GPU interrupt/display behavior and should
not be changed globally to compensate for gameplay timing.

## Reproduce and inspect

Keep game-derived images and raw results under ignored `out/`. The image input
must be a decoded, loaded TU0 PE image in RVA layout; a raw ISO, XEX or on-disk
PE is not that input. The local investigation already has this image:

```powershell
python tools/audit-fixed-timing.py --image out/camera-research/image.bin --sdk out/renderer-sdk-frontend
```

The raw report is `out/performance/fixed-timing-audit.json`, including hashes of
the scanned generated units/image, exact file/line references, nearby instructions,
85,781 unique direct call edges and SDK/source matches. The script itself is
tracked; game-derived excerpts and the image are not.

`tools/sample-crowd.py` can observe a running diagnostic instance without writing
guest memory or taking control of input:

```powershell
python tools/sample-crowd.py --pid <PID> --metadata out/variants/physics-step-down/offsets.json --output out/performance/crowd-new-sample --duration 60
```

It verifies the loaded DLL hashes and pool signature, and records position,
step vector, scalar movement step, animation phase, path references and slot ID.
A 60-second capture completed with 3,750 bulk samples and 63,194 entity rows;
the updated field layout passed a separate five-second live capture. Reads are
asynchronous and may overlap updates. Slots can be recycled even with the same
model and slot ID, so differencing positions can report respawn/transition jumps
as enormous apparent speeds. These samples do not prove velocity or update order.

For the separate background car pool:

```powershell
python tools/sample-background-cars.py --pid <PID> --metadata out/variants/physics-cars/offsets.json --output out/performance/background-cars-new-sample --duration 20
```

This verifies paired DLL hashes and records allocated slots, path references,
position/direction/nominal displacement, countdown, authored speed/multiplier
and visibility classification. It never writes guest memory or sends input.

## Limits and validation

This is a full-source inventory of selected patterns, not an exhaustive proof of
frame-rate independence. Address resolution retains single-definition nonvolatile
registers at joins, but does not prove control-flow dominance. Indirect/vector
constant loads, computed constants, compiler-inlined arithmetic, cross-function
dataflow and gains other than the selected values can be missed. Constants in
mutable data are initial image values, not necessarily live values. Counter
patterns include loops, allocation counts and reference counts as well as
possible frame clocks. Each candidate needs semantic and live validation.

Asset-free scanner tests cover rounded literals, field accumulation, register
clobbers, branch-dependent bases, retained nonvolatile bases and counters kept
separate from timestep evidence. The audit adds no timing changes to the running
game.

At the original audit baseline, all 31 regression tests passed, including the pedestrian and background-car
integration fixtures. The buffered trace test now retries missing
records while rechecking complete contexts and guest memory on every attempt,
respecting the producer's intentional nonblocking drops. Ten consecutive runs
of that previously flaky test also passed.
