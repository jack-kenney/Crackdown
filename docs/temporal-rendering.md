# Temporal rendering investigation — October 5, 2026

Branch: `experimental/temporal-rendering`. The experimental GPU plugin can now
extract a complete scene depth image, reconstruct camera motion and reproject
previous color. This provides a concrete starting point for temporal rendering.
The history mode is a research preview: moving characters, vehicles, particles,
overlaid markers and the HUD still need separate treatment.
`experimental/temporal-and-timing` combines this work with the separate opt-in
[world object timing corrections](fixed-timing-audit.md#experimental-world-object-timing).
Both sets of launchers are available there; the standard executable stays at
its existing launcher path.

## DLSS 5 availability

NVIDIA describes DLSS 5 as 3D-guided neural rendering that enhances rendered
images, distinct from Super Resolution and Frame Generation. Its rasterization
integration uses image and motion information; a static recompilation does not
inherently prevent that integration. See the [NVIDIA developer announcement](https://developer.nvidia.com/blog/whats-new-for-game-developers-dlss-5-with-3d-guided-neural-rendering-nvidia-ace-updates-and-new-rtx-kit-capabilities/).

The concrete integration barrier is the SDK/runtime. NVIDIA's current
[renderer sample](https://github.com/nvpro-samples/vk_gltf_renderer/blob/master/src/dlss.hpp)
gates Neural Rendering behind `USE_DLSSNR` and explicitly requires a beta NGX
SDK. Inspection of the public [DLSS SDK](https://github.com/NVIDIA/DLSS) did not
find a public NR-specific integration interface/runtime. An authorized NR
runtime was also not found in the targeted local NVIDIA installations. Thus this
branch contains no DLSS 5 evaluator or enabled DLSS option.

[Melee Unlocked](https://github.com/Hero88go/melee-unlocked) supplies useful
architectural evidence. Its native GX renderer feeds previous/current vertex
positions into motion output and separates HUD treatment. Its source export
[requires a separately provided NGX NR runtime](https://github.com/Hero88go/melee-unlocked/blob/main/SOURCE_EXPORT.md).
The inspected NR forwarder uses undocumented feature/parameter ABI probing;
copying that would leave this project dependent on an unverified binary contract.
No Melee source was copied into this implementation.

Public [Streamline](https://github.com/NVIDIA-RTX/Streamline/releases/tag/v2.14.1)
supports a conventional DLSS SR/DLAA route. That route still requires coherent
color, depth, motion, jitter, exposure and reset inputs. Adding a loader before
those inputs are dependable would produce an integration with poor image
quality. The current branch instead exercises the buffers on the actual GPU.

## What the game actually draws

TU0 renders its 1280×720 scene in overlapping vertical EDRAM tiles. At 2x draw
scale, its main 2-sample depth surface is allocated at 2560×2048. It is padded
storage, and cannot be resized to the final image as one ordinary screenshot.

The observed first tile uses a 1280×484 scissor. The second uses a 1280×240
scissor and a -480 window Y offset. The second tile belongs at output row 480,
with a four-row overlap. The same depth storage is cleared/reused between them.
Later HUD/postprocessing depth surfaces omit the scene entirely.

The plugin therefore captures the depth surface before each audited resolve
clear and assembles the tiles into a 1280×720 image multiplied by draw scale.
It selects perspective draws that consume vertex constants C0–C3, match the
scene extent and write depth. It accounts for viewport/NDC corrections,
scissors, padding and forward/reversed depth comparisons. It accepts only a
matching camera when assembling subsequent tiles. Incomplete images skip the
temporal presentation pass.

Observed main depth key: `6849488` (base 976, pitch 1280, 2 samples). Selection
uses the draw properties rather than hardcoding that key. This is TU0 research,
with the host-render-target D3D12 path exercised. The ROV path is unsupported.

## Implemented path

`temporal-rendering.patch` applies after the five optimized `frontend` patches.
The plugin owns R32F scene depth, RG16F camera motion, a copy of final RGB10 color
and two RGBA16F history images. History alpha stores depth. These five resources
use approximately 98.4 MiB at 2560×1440, excluding existing renderer allocations.
They persist across frames; resizing retires old resources using GPU submissions.

Camera motion reconstructs a world point from current depth and the inverse
current world-to-clip matrix, then projects it through the previous matrix.
Vectors are **current pixel to previous pixel, in pixel units**. Tests establish
the sign and scale. This accounts for camera motion on static geometry; a
walking pedestrian needs its previous object/bone transform as well.

The history preview samples previous color at that location, rejects mismatched
depth, clamps history against the current 3×3 neighborhood and reduces history
weight when color changes. Invalid depth/projection, gaps, resize and detected
camera cuts reset history. The preview operates after gamma/FXAA, on final color.
It adds no projection jitter and therefore is not production TAA or DLAA.

Depth capture and presentation run on the renderer's direct queue. Ordinary
frames add no CPU readback or separate GPU fence wait. Mode `off` creates no
temporal resources. GPU/shader/allocation failures leave presentation alone
where the renderer can continue; they do not recover a failed D3D12 device.

Two important integration errors were caught and corrected in real game runs:

- The shared constant-upload pool is reclaimed by completed **frame**, whereas
  other resources retire by completed **submission**. Using submission numbers
  for constant uploads pinned the shared draw pages and exhausted memory.
- `RequestOneUseSingleViewDescriptors` returns non-contiguous slots on the
  bindless path. Each temporal texture now has an independent one-descriptor
  root table. Treating the slots as contiguous produced black color and incorrect
  history bindings. The GPU fixture deliberately scatters its descriptors.

## Validation

The standalone fixture compiles the exact embedded renderer HLSL with the same
optimization/strictness flags. It runs on the RTX 5070 Ti and Windows WARP.
Both passed cropped/padded odd-sized depth, overlapping tile assembly, 2x/4x
nearest-sample depth in both Z conventions, projection inversion, viewport
correction, camera cuts, vector sign/scale, disocclusion, history reset and invalid
projection rejection. It also passed 256 ping-pong frames with the actual RGB10,
RG16F and RGBA16F formats, including subnormal reversed depth.

Separate game profiles reached garage gameplay in `off`, depth, motion,
capture-only and history modes. Scene depth visibly covers the full image and
player rather than stretching one EDRAM tile. History rendered visible RGB after
the descriptor correction. A two-minute stationary history sample submitted
13,810 frames (115.1/s), with 241 memory samples and a peak private-memory rise
of 31.1 MiB. It completed without upload/backend/device errors. This verifies
progress and bounded memory in that scene, not image quality or a performance
comparison. The local D3D12 debug layer is unavailable; debug-layer validation
is not claimed.

The combined branch also passed a two-minute history run with 13,600 submitted
frames and 42.6 MiB peak additional private memory. A stricter subsequent pan
check excludes unused camera-transform lanes, which contain changing scratch
data, and confirms a rotation change in the nine XYZ components. After the
remaining intel screens were dismissed, a further 30-second sample submitted
3,492 frames with 1 MiB additional private memory and no backend errors.

Raw local evidence stays under ignored `out/temporal/`, particularly
`session-20261005-195807-3600` (assembled depth),
`session-20261005-195939-5427` (motion visualization), and
`session-20261005-202038-8082` (corrected history captures and `soak.csv`).
DLL hashes accompany the recordings. Game images and assets are not committed.
The combined run is `session-20261005-205212-8407`; the initial two-minute result
is `out/temporal/combined-smoke-output.json` and the stricter later result is
`out/temporal/combined-pan-verified.json`. The latter replaces the session's
`smoke.json` / `soak.csv`; both result summaries remain available. Those rates
are scene-specific frame submission counts, not a measured performance gain.
A fresh depth-mode startup at `session-20261005-210224-7195` passed the final
camera check after two dismissal attempts, then submitted 1,792 frames over
15 seconds. Its final capture shows complete scene/player depth. This validates
the updated startup path separately from the earlier memory-soak results.

## Build and try

Build the performance executable first, then the isolated GPU variant. SDK paths
have the same defaults as the other renderer experiments:

```powershell
./build-local.ps1 -PerformanceTest
./tools/build-renderer.ps1 -Variant temporal
./Play-Crackdown-Temporal.cmd -Mode depth
./Play-Crackdown-Temporal.cmd -Mode motion
./Play-Crackdown-Temporal.cmd -Mode history
```

Close each diagnostic instance before launching another comparison. The
launcher copies progress into a fresh `out/temporal/session-*` profile on every
launch, excluding shader cache. It defaults to 120 FPS and reads saved graphics
settings. `-FrameRate 60`, `144` and `240` are also supported. `-Mode off` runs the
control; `-Mode capture` captures depth while keeping ordinary color presentation.
Use `-ProfileSource <directory>` to choose the profile to copy, or `-Trace` to
record sampled shader constants, depth surfaces and resolve information.

For a bounded automated test of the default campaign choices on a private profile:

```powershell
./tools/launch-temporal.ps1 -Mode history -Automation -ProfileSource out/temporal/session-trace/userdata |
    ConvertTo-Json | Set-Content -Encoding utf8 out/temporal/test-session.json
python tools/temporal-smoke.py --session out/temporal/test-session.json --load --pan --soak 120
```

Replace the example profile source with an existing local profile. Automatic menu
navigation expects its default campaign choices; it does not select arbitrary
unlocked supply points. The sampler checks the loaded DLL hashes, sends bounded
controller leases, verifies that the main camera actually turns, captures guest
output, checks frame progress and records
private memory. The soak raises an error on upload/backend/device failures or
more than 512 MiB additional private memory by default. Initial intel playback
may ignore Back; the load path retries its dismissal for up to 35 seconds and
fails if the camera still cannot turn. Captures still need
inspection: advancing frame counters alone cannot detect a black image.

## Remaining work, in order

1. Associate draws with stable guest entity/mesh identities, retain previous
   rigid transforms and then previous skinning poses. A shader hash or draw
   ordinal alone cannot identify moving objects across culling/LOD changes.
2. Produce per-surface motion alongside depth, including overlapping EDRAM tiles,
   depth-tested foregrounds, viewmodels, sky and appropriate particle treatment.
3. Separate HUD/markers from history and determine the correct pre/post-gamma
   scene color boundary. Avoid temporally filtering screen-fixed text using
   the world surface behind it.
4. Apply consistent subpixel jitter to scene projections, remove jitter from
   motion and retain unjittered culling/marker calculations. Validate camera,
   FOV, zoom, vehicle transitions, cuts and resolutions.
5. Evaluate usable TAA/DLAA/SR quality, then integrate the documented DLSS 5
   interface once its authorized SDK/runtime is available. Profile the existing
   recorded city route rather than drawing conclusions from the garage.

Depth and motion extraction are feasible in this renderer. Reliable moving-object
history is the substantial renderer task; the private NR interface is an
additional external dependency.
