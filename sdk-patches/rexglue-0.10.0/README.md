# Renderer performance experiments

`type0-register-batching.patch` applies to ReXGlue SDK v0.10.0, commit
`f5337cdc947ff6d4c4196737e2c807a48f2a1fc2`. It changes the GPU plugin only.
The installed SDK and the regular Crackdown build remain unchanged.

The follow-up `single-range-allocation.patch` keeps a single shared-memory
request on the stack instead of allocating vector storage. It retains the
locked page-validity scan, residency checks and upload behavior. The separate
`constant-packing.patch` uses a local bit scan while gathering shader constants,
removing one exported runtime call per constant without changing their bytes
or order. Both patches build on the register-batched renderer.

`control-register-batching.patch` extends bulk processing to contained
`0x2000–0x3FFF` control-register writes. It preserves ordered volatile stores
and uses the original path when debug logging is enabled. Scratch, coherency,
gamma, mixed ranges and repeated-register packets keep their existing behavior.
`command-stream-reuse.patch` retains constructed command storage between
submissions and records a separate used length. Replay reads only that prefix,
avoiding zeroing storage that command writers immediately overwrite.

The separate `constant-reuse.patch` compares and copies clean shader constants
in one SIMD pass. Identical values can retain their existing constant buffer;
dirty bindings keep the ordinary copy. Comparison uses the same loaded bits
that are stored, including NaN payloads and signed zero. Fetch notifications
and residency invalidation remain unconditional. This candidate has a cost
when constants change and is excluded from `frontend`: the combined comparison
did not establish additional FPS. It remains available for further scene tests.

## Why this path

On October 4, 2026, two 20-second live samples at exactly the same player
position measured 31.55 guest FPS looking over the city and 112.21 FPS looking
straight down. The city view consumed approximately one full graphics command
thread while device-wide GPU utilization was 32–38%. Looking down raised GPU
utilization to 95–99%. Settings were 1440p, extreme FXAA, 4x filtering, lighting
fix enabled, a 240 FPS native target, and a 15 m first vehicle LOD threshold.

A separate five-second instruction-pointer sample of the busy view collected
328 graphics-thread observations. The TYPE0 loop, base and D3D12 register-write
methods, and register-metadata lookup together accounted for 43.0% of them.
This is an approximate sampled cost, not a predicted FPS improvement. A later
street sample measured 37.10 FPS at a different player position; it is not a
same-scene return measurement. The earlier death/respawn sample is excluded.

The sample artifacts remain local under `out/performance/live-38108`. Counters
measure guest submissions, not monitor presentations. CPU enumeration sometimes
delayed the sampler, so large observation gaps are not established game hitches.

## Change

Contiguous TYPE0 register packets now use the existing
`WriteRegisterRangeFromRing` path. ReXGlue already batches shader and fetch
constants there for other packet types. This avoids per-word virtual dispatch,
metadata lookup and repeated dirty-state bookkeeping. Repeated writes to a
single register still use the scalar path. Mixed ranges, scratch writeback,
coherency, gamma and extended registers retain the existing scalar fallback.
Byte swapping and ring wrapping use the existing range helper.

This does not change draw distance, shaders, textures, lighting, scene population
or the game's timestep. The launcher keeps the existing timing and event fixes
enabled so renderer comparisons change only the plugin.

## Build and compare

Requirements: the normal Windows build prerequisites, the released 0.10.0 SDK,
and a Git source checkout of that exact SDK tag. GPU compilation uses the
installed runtime and dependencies; SDK submodules need not be initialized.

```powershell
git clone --branch v0.10.0 --depth 1 https://github.com/rexglue/rexglue-sdk.git C:\src\rexglue-0.10.0
.\build-local.ps1 -PerformanceTest -SdkPath C:\sdk\win-amd64
.\tools\build-renderer.ps1 -SdkPath C:\sdk\win-amd64 -SourcePath C:\src\rexglue-0.10.0
.\tools\build-renderer.ps1 -Baseline -SdkPath C:\sdk\win-amd64 -SourcePath C:\src\rexglue-0.10.0
.\tools\build-renderer.ps1 -Variant optimized -SdkPath C:\sdk\win-amd64 -SourcePath C:\src\rexglue-0.10.0
.\tools\build-renderer.ps1 -Variant frontend -SdkPath C:\sdk\win-amd64 -SourcePath C:\src\rexglue-0.10.0
```

In this development workspace, SDK/source paths are detected under `../.tools`.
The helper creates an isolated worktree at `out/renderer-sdk-source`, verifies
its complete changes against the patch, runs the register regression suite,
builds an optimized DLL with symbols, and stages a copy of the performance game
in `out/variants/renderer-batched` or `renderer-baseline`. It refuses to replace
a running variant. `-PluginOnly` skips executable staging. Additional variants
use their own `out/renderer-sdk-<variant>` worktrees, and complete combined
diffs are verified against their patch lists, including new source files.

| `-Variant` | Changes relative to the released SDK |
|---|---|
| `baseline` | Original TYPE0 loop, built with the same compiler |
| `batched` | TYPE0 batching (the default comparison launcher) |
| `ranges` | Batching plus the single-range allocation fast path |
| `constants` | Batching plus local shader-constant bit scans |
| `optimized` | TYPE0 batching, single-range allocation and constant packing |
| `controls` | `optimized` plus control-register batching |
| `stream` | `optimized` plus command-storage reuse |
| `frontend` | `optimized` plus both control batching and command-storage reuse |
| `reuse` | `optimized` plus experimental shader-constant reuse |
| `frontend-reuse` | `frontend` plus experimental shader-constant reuse |
| `gpu` | Batching plus optional GPU timestamp instrumentation |
| `optimized-gpu` | The original three CPU changes plus optional GPU timestamps |

```powershell
.\Play-Crackdown-Renderer-Test.cmd
.\Play-Crackdown-Renderer-Test.cmd -Baseline
.\Play-Crackdown-Renderer-Test.cmd -FrameRate 60 -VehicleLod1Distance 0
.\Play-Crackdown-Renderer-Optimized.cmd
.\Play-Crackdown-Renderer-Test.cmd -Variant constants
```

The dedicated launcher defaults to the profiled 240 FPS target and 15 m vehicle
threshold. `Play-Crackdown-Renderer-Optimized.cmd` selects `frontend`; use
`Play-Crackdown-Renderer-Test.cmd -Variant optimized` for the previous build.
All variants use the same saved graphics choices and
`out/userdata-renderer-test` profile. It copies progress from the performance
profile on first launch and rebuilds its own shader cache. Run the variants
sequentially; the launcher rejects concurrent use of that comparison profile.
The regular and performance launchers retain their existing defaults.

For a valid comparison, use the same supply point, player position, camera,
settings and warmed scene. Compare alternating baseline/batched runs after
startup shader compilation. Each variant includes `offsets.json` paired with
its own DLL hashes for the existing read-only benchmark tools. The baseline
uses the same compiler and flags, with the original TYPE0 source from Git;
comparing only against the downloaded DLL could include compiler differences.

## Validation

`tests/renderer_type0_test.py` compiles the actual original and patched TYPE0
methods, real RingBuffer code, and real base/D3D12/Vulkan register-write methods
against state fixtures. All 8,814 cases per backend pass (17,628 total), checking
register bytes, dirty constant buffers, texture/vertex residency, scratch
writeback, coherency, gamma, extended registers, truncation, repeated-register
packets, maximum count and wrapped payloads. No GPU is created by these tests.
An informational microbenchmark measures register processing only; its ratios
must not be presented as whole-game speedups.

Game testing and performance observations are recorded in
[the frame-rate investigation](../../experiments/README.md#renderer-cpu-investigation).
The matching Agency view measured 92.6 guest FPS with batching versus
61.3–63.0 without it. Subsequent extended hands-on city play reported 68.0
average FPS, with rare dips below roughly 55 FPS. Those city figures are
player-reported overlay observations, not a measured minimum or percentile.
Vulkan is covered by the method regression but is not built or exercised in a
game by this Windows helper. A controlled city-route comparison and broader
campaign coverage remain open validation work.

The range fixture passes 8,526 original/patched comparisons, including aliasing,
invalid bounds, page/block boundaries, invalidation and upload failures. A
warmed 500,000-request CPU fixture observes 500,000 temporary allocations in
the original single-range path and zero in the patched path. The constant
fixture passes 23,192 original/patched/oracle comparisons across both shader
banks, sparse/dense maps, unaligned destinations and arbitrary float payloads.
These fixture timings are not whole-game speedups.

Control-register regression executes real original/patched methods and the
actual GPU logger: 33,516 comparisons pass for each of clang++ and clang-cl,
covering register bytes, special side effects, logging levels and wrapped
payloads. The deferred-command fixture passes 732,832 checks, including every
opcode, variable payloads, nullable arguments, alignment, growth and shorter
submissions after reset. Retained storage is poisoned before recording to
expose any dependence on its former zero initialization. These tests create
no GPU device; live rendering is checked separately.

The constant-reuse fixture passes 10,201 single-variant cases and 10,221 when
combined with control batching. It checks exact uploaded bytes, stage and shader
layout changes, partial/unaligned writes, signed zero and NaN payloads, and a
source-mutation case proving the compared value is the one stored. TYPE0 tests
for this variant prove any retained clean binding has identical referenced bytes;
other variants keep exact original dirty-state comparisons. Fetch invalidation
remains exact in every variant.

## GPU measurements

The profiler is optional and operates only in the `gpu` and `optimized-gpu`
variants. A separate timestamp heap and readback ring reuse entries only after
the existing submission fence completes and the CPU consumes the results.
Ring pressure drops samples rather than waiting or changing submission timing.
An empty output path disables profiling.

```powershell
.\tools\build-renderer.ps1 -Variant optimized-gpu
.\Play-Crackdown-Renderer-Test.cmd -Variant optimized-gpu -GpuTimingPath out\gpu-whole.csv
# A separate run, with sampled category markers:
.\Play-Crackdown-Renderer-Test.cmd -Variant optimized-gpu -GpuTimingPath out\gpu-details.csv -GpuTimingDetails
```

The default samples every 60 guest frames; `-GpuTimingInterval` selects a
different interval. Start with whole-submission timing before enabling detailed
markers: timestamp queries measure completion of preceding work and can affect
GPU pipelining. Categories cover render-target updates, texture conversion,
uploads, resolves/readbacks, gamma and FXAA. Their intervals are inclusive and
can overlap, so adding category times does not produce a frame total.

CSV rows describe **guest command submissions**, which may span multiple rows
per guest frame. They exclude the presenter's separate command list, CPU
pipeline compilation, CPU waits and monitor presentation. Query ticks use the
direct queue's frequency. See Microsoft's [timestamp semantics](https://learn.microsoft.com/en-us/windows/win32/direct3d12/timing),
[query rules](https://learn.microsoft.com/en-us/windows/win32/direct3d12/queries)
and [readback synchronization](https://learn.microsoft.com/en-us/windows/win32/direct3d12/readback-data-using-heaps).

For read-only frame, CPU and optional device-wide GPU observations:

```powershell
python tools/sample-renderer.py --pid <PID> --seconds 30 --offsets out/variants/renderer-optimized-gpu/offsets.json --output out/profile-sample --gpu
python tools/summarize-gpu-timing.py --input out/gpu-details.csv --frames out/profile-sample/frames.csv --output out/profile-sample/gpu-summary.json
```

The frame sampler uses separate workers for CPU enumeration, guest-state reads
and `nvidia-smi`; those operations no longer block counter polling. It records
actual polling gaps and read windows. Frame intervals remain sampled estimates,
not physical presentation timestamps. Every worker verifies the process and
matching DLL hashes. The GPU summary excludes incomplete, dropped or invalid
sampled frames and reports rejection counts.
