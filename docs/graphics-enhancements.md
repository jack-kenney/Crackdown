# Graphics enhancement tests

## Texture filtering

The launcher now exposes the SDK's anisotropic override: game settings, off,
1x, 2x, 4x, 8x and 16x. Existing preferences keep the previous 4x default.
16x uses `--anisotropic_override=5`; 4x uses `--anisotropic_override=3`.

Separate 1440p Agency garage sessions reached gameplay and completed ten-second
camera sweeps with 4x and 16x. Both submitted approximately 30 frames per second,
with no sampled interval over 50 ms. Another test instance was running during
these checks. They establish compatibility in that scene; the 30 FPS cap and
concurrent instance prevent using them to claim equal GPU cost or city headroom.
Use the same recorded city route for a performance comparison.

## Experimental field of view

The experimental build adds `--camera_fov_scale=1.2`, a multiplier for TU0's
reference camera angle. `1.0` preserves the original camera. Values from 0.75
through 1.5 are accepted, with a restart required. This is not a degrees setting:
the game transforms its reference angle before constructing the projection.

The hook runs the original camera snapshot routine, then changes only the angle
in the main player's active default-camera snapshot. It also adjusts the tangent
input in the two TU0 camera routines that build the cached world-to-screen
projection and simulation frustum. All three paths use the same scaled effective
angle. The source camera angle, aiming/zoom multiplier, transform, near/far planes
and other camera modes remain intact. The snapshot address comes from the
completed guest routine, avoiding a second read of a changing buffer index.
Invalid angles and unrelated tangent calls are preserved.

Native checks execute the actual generated TU0 routine and verify the disabled
default, guest-register preservation, all three buffer indices, no accumulated
scaling, other-mode exclusion and invalid-angle handling. They also execute the
actual cached projection, world-to-screen and frustum routines across 0.75–1.5
scales and normal/zoomed angles, comparing their results with a stock camera at
the render snapshot's effective angle.

### Overhead marker drift correction — October 5, 2026

Hands-on testing found that a wider FOV displaced overhead Agency icons from
friendly cops. The first prototype changed only the render snapshot; TU0's
`sub_82296748` and `sub_82295D10` still built projection/frustum caches using the
original angle. `sub_822969E8` places world markers using the former cache.
The correction intercepts the tangent calls at those two specific call sites,
with the same main-player/default-camera guards as the snapshot hook. It keeps
the game's original math and float rounding, without temporarily overwriting
shared camera memory. The projection regression covers marker positions at
multiple depths. Full city gameplay validation of this correction remains
pending. A separate 1440p garage session with the rebuilt optimized executable
at scale 1.5 reached gameplay and accepted gunfire. Read-only inspection found
82.5 in the active render snapshots with the source angle still 55, and both
cached screen-projection coefficients matched the 82.5-angle reference within
4e-8. The corresponding 55-angle coefficients were different. The test profile
was separate from the player's saves, and the test instance closed normally.
Local evidence: `out/camera-research/projection-fixed-validation/`.

Two isolated 1440p garage launches from the same copied profile validated the
compiled hook. With scale 1.0, active snapshot reference angles remained 55;
with 1.2, they became 66 while the source camera angle remained 55. Renderer
captures visibly show a wider view with unchanged HUD layout. Local captures:

- `out/camera-research/runtime-default/automation/31656/frame-5.png`
- `out/camera-research/runtime-wide/automation/50408/frame-1.png`

The wider-view session also accepted jumping, movement, gunfire and camera
rotation and continued rendering. Both owned camera test instances closed
normally. These short garage checks do not establish full gameplay compatibility.

Vehicle camera transitions, lock-on aiming, clipping/culling during city
movement, and campaign cinematics still need testing. This setting remains
outside the Windows settings launcher. It is compiled into the current optimized
build and can be configured through F4 → Game Enhancements → `camera_fov_scale`
(Save to config, then restart), or a startup flag. The stock build excludes it.

## Building experiments separately

From the repository root, with the installed SDK and extracted TU0 assets:

```powershell
.\build-local.ps1 -Experiments -SdkPath C:\path\to\rexglue-sdk\win-amd64

out\build\win-amd64-experiments\crackdown.exe --game_data_root=assets --user_data_root=out/fov-userdata --log_file=out/fov-game.log --draw_resolution_scale_x=2 --draw_resolution_scale_y=2 --camera_fov_scale=1.2
```

The helper builds into a different directory and stages matching SDK DLLs beside
that executable. It does not replace the normal Release executable. Normal
builds explicitly disable `CRACKDOWN_BUILD_EXPERIMENTS`. In a manual CMake build,
use a separate binary directory and `-DCRACKDOWN_BUILD_EXPERIMENTS=ON`.

The same experimental build includes the disabled-by-default frame-rate pacing
prototype. Leave `fps60_pacing_experiment` false for FOV testing: enabling it
accelerates gameplay. See [the frame-rate research](../experiments/README.md).

For texture package loading and the verified frontend replacement, see
[texture enhancement notes](texture-enhancements.md).
