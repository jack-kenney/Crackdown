# Renderer register batching experiment

`type0-register-batching.patch` applies to ReXGlue SDK v0.10.0, commit
`f5337cdc947ff6d4c4196737e2c807a48f2a1fc2`. It changes the GPU plugin only.
The installed SDK and the regular Crackdown build remain unchanged.

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
```

In this development workspace, SDK/source paths are detected under `../.tools`.
The helper creates an isolated worktree at `out/renderer-sdk-source`, verifies
its complete changes against the patch, runs the register regression suite,
builds an optimized DLL with symbols, and stages a copy of the performance game
in `out/variants/renderer-batched` or `renderer-baseline`. It refuses to replace
a running variant. `-PluginOnly` skips executable staging.

```powershell
.\Play-Crackdown-Renderer-Test.cmd
.\Play-Crackdown-Renderer-Test.cmd -Baseline
.\Play-Crackdown-Renderer-Test.cmd -FrameRate 60 -VehicleLod1Distance 0
```

The dedicated launcher defaults to the profiled 240 FPS target and 15 m vehicle
threshold. Both variants use the same saved graphics choices and
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
