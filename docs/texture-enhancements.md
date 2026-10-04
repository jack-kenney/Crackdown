# Texture replacement investigation: TU0

The first practical replacement target is a frontend or map PNG in an XUI
package. A same-dimension frontend PNG replacement has been visually validated
in an isolated standard-build session. The repository now has a package
inspector and copy/replacement tool; production mod activation and gameplay
environment textures need further work.

## Verified locally, October 4, 2026

Read-only inspection of the user's extracted TU0 assets found:

| Asset family | Findings | Suitable first experiment |
| --- | --- | --- |
| `XUIFrontEnd/*.xzp` | Twelve uncompressed XUIZ v1 packages; 1,013 entries, including 861 standard PNGs and compiled XUI layouts. | Replace one title/menu or map graphic without changing its pixel dimensions. |
| `vfx_textures.tpk` | 122 named textures with Xbox 360 texture descriptors and a raw resource payload. Names include ability icons, clouds, particles and bullet decals. | Decode a single ability icon, then prove a same-size replacement with identical compression/mips. |
| `ScreenRenderer/LobbyCube.dds` | Standard DDS header: 256×256 DXT3 cubemap, six faces, 393,344 bytes. | A small standalone resource test, after establishing where this cubemap is used. |
| `*.dff` | `fullcity.dff` and `global.dff` are zlib streams containing serialized game data. Their inflated sizes are 23,058,867 and 51,206,014 bytes. | Resource metadata investigation; these are not ordinary GTA-era DFF model files. |
| `Streaming/*.resblock[c]` | Large raw resource blocks; their leading bytes do not contain ordinary DDS/PNG headers. | Environment/character enhancement requires resolving resource metadata and GPU layout first. |

All twelve XUI packages were parsed and repacked in memory byte-for-byte.
Every PNG passed chunk-boundary/CRC checks. An unchanged copy of `Scene.xzp`
was written only under ignored `out/texture-research`; the source SHA256
remained `9ca02bfedd706fce30197156edeb7aeea6751f0cdd6c111fac56ef020ae5f95d`.
This establishes package format and preservation of contents. A subsequent
runtime prototype below establishes frontend resource replacement, without
claiming an image-quality improvement.

## Verified runtime prototype

An ignored alternate asset root at `out/texture-research/runtime-assets` was
created using junctions for unchanged asset directories and hard links for
unchanged leaf files. `XUIFrontEnd` is a real new directory. Its `Scene.xzp`
is a separately created file with link count one, containing exactly one
replacement: `Start Button.png`. All 696 other resources retain their original
names, order and bytes.

The replacement is a fresh 128×128 procedural cyan/magenta checkerboard with
yellow `MOD` lettering, matching the original resource dimensions. It is a
diagnostic marker, not finished enhancement artwork, and is stored only in
ignored `out`. No original image pixels were modified.

The standard executable was launched with the alternate `--game_data_root`,
fresh separate user data, a separate log and `--automation=true`, at the
original 1280×720 rendering resolution. The title screen displayed the marker
above `Press START to play`. Renderer capture:

`out/texture-research/start-button-session/automation/17044/frame-6.png`

The test instance was closed through its own window and exited with code zero.
The original package SHA256 remained unchanged. The user's running game,
executable, save files and launcher preferences were not changed. Staged-package
metadata and launch arguments are saved locally under `out/texture-research`.

An earlier trial replacing `LogoCrackdown.png` did not change the visible title
logo. That asset's presence does not establish that the active title sequence
uses it. The Start button provides a confirmed resource target.

This test establishes package loading and same-size PNG replacement. It does
not establish higher-resolution XUI behavior, gameplay HUD replacement,
environment texture replacement or performance costs. An additional 2× PNG
test with unchanged layout is the next UI enhancement experiment.

## XUI package tool

`tools/xzp_assets.py` uses the Python standard library. The supported XUIZ v1
header is big-endian: magic, version, total length, zero flags, table length,
then a 16-bit entry count. Table length excludes the count itself. Each entry
contains a 32-bit payload size, 32-bit relative payload offset, an 8-bit UTF-16
code-unit count and the UTF-16BE name. TU0's payloads are contiguous in table
order. The tool rejects unsupported layouts rather than discarding unknown
bytes while rebuilding them.

From the repository root:

```powershell
python tools/xzp_assets.py list assets/XUIFrontEnd/Scene.xzp

python tools/xzp_assets.py extract assets/XUIFrontEnd/Scene.xzp `
  --entry 'Start Button.png' --output out/texture-research/start-button

python tools/xzp_assets.py copy assets/XUIFrontEnd/Scene.xzp `
  --output out/texture-research/Scene-copy.xzp

# Provide your own authored PNG; this creates a separate package, not a mod install.
python tools/xzp_assets.py replace assets/XUIFrontEnd/Scene.xzp `
  --entry 'Start Button.png' --png out/texture-research/replacement.png `
  --output out/texture-research/Scene-custom.xzp
```

`Start Button.png` is 128×128. Other candidates in `Scene.xzp` include
`xui assets2\wepHud.png` (190×129), `xui assets2\WorldMap_Main_002.png`
(983×983), and 24×24/48×48 map icons. File names alone do not establish that
an image is used by the active gameplay HUD. The title button or a visible map
icon is a clearer initial test than promising a complete sharper HUD.

Replacement requires an exact, unambiguous entry name and a PNG with valid
chunk CRCs. Dimension changes require `--allow-resize`: this is an experiment
because compiled XUI layout, cropping and UV behavior still need checking.
Image decoding and in-game use remain separate validation steps.

Sources are read-only, output files use exclusive creation, and extraction
requires a new directory. There is no overwrite option. Unsafe Windows paths
and extraction path conflicts are rejected before creating the directory.
Two original map packages contain duplicate names; listing/copying preserves
them, while ambiguous replacement/extraction is rejected. Extract a specific
unique entry from these packages instead of extracting all entries together.

Run the independent regression checks with:

```powershell
python tests/xzp_assets_test.py
```

The six tests cover byte-preserving roundtrips, replacement preservation,
malformed extents/header lengths, path traversal and Windows aliases,
duplicate names, PNG CRC/size checks and prevention of overwriting originals
or existing hard links.

## Loading a modified package

The current host mounts one asset directory through `--game_data_root`, mapped
to both `game:` and `d:` by ReXGlue's `Runtime::SetupVfs`. The custom `cache:`
device is an in-memory writable cache; it does not redirect asset reads or
provide a texture mod directory. Adding loose PNGs beside an XZP does not
automatically replace its entries.

A safe runtime prototype uses either a genuinely separate asset tree with
the modified XZP at its original relative path, or a new host filesystem
overlay that selects modified packages before the base asset directory.
An overlay is preferable for ongoing mod work, but is not implemented yet.
The existing development asset directories share originals through junctions
and hard links; editing files through these paths would alter the original
extraction. Keep repacked files under `out` until an isolated launch path is
available. Use a separate user-data directory for experimental sessions.

Before enabling higher-resolution assets, establish a same-dimension visibly
different replacement, confirm it appears in the expected screen, and verify
startup, loading and rendering. Then test a 2× replacement with unchanged XUI
layout and compare screenshots. This separates package plumbing from scaling
behavior. It does not require replacing the renderer.

## Gameplay texture pack next step

The observed `vfx_textures.tpk` layout accounts for every byte:

1. Three big-endian words: count **122**, name-table size **1,904**, raw payload
   size **23,085,056**.
2. 122 big-endian name offsets; ASCII null-terminated names begin at byte **500**.
3. Names end at byte **2,404**, followed by 122 descriptors of **52 bytes** each.
4. Raw texture data begins at byte **8,748**.

The final six words of each descriptor match ReXGlue's
`xe_gpu_texture_fetch_t` layout: format, endianness, size, tiling and mip fields.
For example, `Firearms_Icon_Opac` has a 128×128 2D tiled texture fetch with
format code 20 (DXT4/5). Base/mip address fields appear to refer to pages within
the resource payload; that interpretation still requires a successful decode.

A useful next tool can reuse the SDK's Xenos tiling/layout calculations to
export one texture and its mips, then rebuild a same-size replacement while
preserving descriptor metadata and payload extents. Larger replacements need
new allocation offsets, fetch dimensions/pitch and mip layout. Standard PNG
or DDS upscaling followed by blindly inserting bytes is insufficient for these
native GPU resources. An ability icon or cloud texture would exercise this
path without solving the entire city's streaming format.

## Other projects and tools

[matty45/Crackdown2-ArchiveTool](https://github.com/matty45/Crackdown2-ArchiveTool)
describes support for Crackdown **2** uncompressed `.pack`/`.pack.toc` containers.
It does not claim compatibility with Crackdown 1's `.resblock`, `.dff` or `.tpk`
assets. Shared RenderWare ancestry makes its research useful background, not a
drop-in extraction/repacking solution.

The [August 7, 2026 community update](https://www.reddit.com/r/Crackdown/comments/1vi359b/crackdown_2_modding_update/)
reports successful Crackdown 2 character model/texture extraction and ongoing
weapon/vehicle/skeleton work. Those are the authors' claims about another game,
not local validation of our TU0 resources. The earlier
[April 15 texture-repacking report](https://www.reddit.com/r/Crackdown/comments/1smbcw7/crackdown_2_modding_now_somewhat_a_reality/)
links the archive tool and describes a title-screen test.

For renderer metadata, the primary implementation reference is
[ReXGlue v0.10.0 Xenos texture fetch definitions](https://github.com/rexglue/rexglue-sdk/blob/v0.10.0/include/rex/graphics/xenos.h).
The current resource mounting path is documented directly by
[ReXGlue v0.10.0 Runtime::SetupVfs](https://github.com/rexglue/rexglue-sdk/blob/v0.10.0/src/system/runtime.cpp).
The new XUIZ utility was derived from the user's local headers and verified
against those packages; it needs no proprietary Xbox UI authoring tools.
