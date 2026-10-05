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
| Distant crowd movement | `82330B68`, `82342470` and `82338220` calculate object field +180 using walk speed times 1/30 or run speed times 1/15. `82331198` advances position +128 by a stored step vector +112 and decrements segment steps +176 once per call. | Strong candidate for distant NPC/car movement. Measure actual invocation cadence and transitions before changing the authored steps. |
| Distant crowd animation | `82330888` advances phase +228 using multiplier +184 times `0.0333333`, then wraps against clip length and evaluates the animation. | Strong candidate for the reported fast distant animations. This rounded literal was absent from the earlier exact 1/30 search. Confirm cadence alongside movement. |
| Object state timer | `8252EDA0` adds 1/30 to field +2404 each call before its state machine. | A real per-call time accumulator. Object role and call frequency remain unresolved; no demonstrated connection to interrupted Agency speech. |
| Character proxy timer | `822AB708` contains a branch adding 0.05 to a component field. | Audit the branch, field role and accumulated-time consumers. A fixed value here does not alone prove that it drives movement. |
| Two related object state timers | `8259A738` and `8259AD98` add 0.05 to field +368 and compare it with another threshold. | Verify object identity and how often these states run. |
| Object fade | `82531228` increments/decrements a value by 1/15 and copies it to four adjacent fields. | Likely a per-update fade; verify field meaning and visible duration. |
| Input/movement response | `8228B0F0` applies a 1/30 proportional decay to field +6980 each call; `822A2C90` has another fixed-step response. | Inspect whether these are response coefficients, deadlines or calibrated geometry. Normalize only after identifying their elapsed-time behavior. |
| Entity displacement/impulse candidate | `82354560` multiplies a scalar and a direction vector by 1/60 before virtual dispatch. | Identify the callback and receiving setter. An event invoked once per impact needs a different treatment from an update invoked every frame. |
| Shared dt getter | `8254C568` has 73 direct caller functions and selects fixed or published clock-derived timing. | Existing native clock tests exercise the elapsed-time branch. Audit callers that assume the original reference interval separately. |
| Player step-down gain | `822B4D40` contains the 0.4 correction gain. | Human-tested elapsed-time correction is enabled by default in native timing. See [physics investigation](physics-investigation.md). |
| Frame-count conversions | `82265C70` converts a frame count with 1/30; logical engine deadlines also use frame indices. | Preserve the meaning of authored frame-count data. Changing its conversion is not equivalent to changing simulation dt. |

The distant crowd uses dedicated LOD4 walk/run clips and LOD4/LOD5 civilian car
assets. Its pool has a signature of `0xACED0FF0`, 1,500 slots of 672 bytes,
starting at `0x82E64090`. This is separate from the nearby Havok character
controller traced for the player's step-down correction. It fits the user's
distance-dependent observation, but call frequency and causal fixes are still
unverified. The crowd also smooths steering with a fixed per-update coefficient;
that response deserves an elapsed-time audit even after movement is corrected.

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

All 29 regression tests passed. The buffered trace test now retries missing
records while rechecking complete contexts and guest memory on every attempt,
respecting the producer's intentional nonblocking drops. Ten consecutive runs
of that previously flaky test also passed.
