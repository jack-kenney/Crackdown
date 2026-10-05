param(
    [string]$SdkPath = $env:REXGLUE_SDK_ROOT,
    [string]$SourcePath,
    [switch]$Baseline,
    [ValidateSet('baseline', 'batched', 'ranges', 'constants', 'optimized', 'gpu', 'optimized-gpu', 'controls', 'stream', 'reuse', 'frontend', 'frontend-reuse')]
    [string]$Variant = 'batched',
    [switch]$PluginOnly,
    [ValidateRange(1, 64)][int]$Jobs = 4
)
$ErrorActionPreference = 'Stop'
$originalGitIndex = $env:GIT_INDEX_FILE
$env:GIT_INDEX_FILE = $null
try {
if ($Baseline) {
    if ($Variant -ne 'batched' -and $Variant -ne 'baseline') { throw '-Baseline cannot be combined with another -Variant.' }
    $Variant = 'baseline'
}
$Baseline = $Variant -eq 'baseline'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $SdkPath) { $SdkPath = Join-Path $root '../.tools/rexglue-v0.10.0/win-amd64' }
if (-not $SourcePath) { $SourcePath = Join-Path $root '../.tools/rexglue-source-v0.10.0' }
$SdkPath = (Resolve-Path -LiteralPath $SdkPath).Path
$SourcePath = (Resolve-Path -LiteralPath $SourcePath).Path
$revision = 'f5337cdc947ff6d4c4196737e2c807a48f2a1fc2'
if ((& git -C $SourcePath rev-parse HEAD) -ne $revision -or $LASTEXITCODE -ne 0) {
    throw 'The renderer experiment requires the exact ReXGlue v0.10.0 source revision.'
}
$patchNames = @('type0-register-batching')
$optimizedBase = $Variant -in @('optimized', 'optimized-gpu', 'controls', 'stream', 'reuse', 'frontend', 'frontend-reuse')
if ($Variant -eq 'ranges' -or $optimizedBase) { $patchNames += 'single-range-allocation' }
if ($Variant -eq 'constants' -or $optimizedBase) { $patchNames += 'constant-packing' }
if ($Variant -in @('controls', 'frontend', 'frontend-reuse')) { $patchNames += 'control-register-batching' }
if ($Variant -in @('stream', 'frontend', 'frontend-reuse')) { $patchNames += 'command-stream-reuse' }
if ($Variant -in @('reuse', 'frontend-reuse')) { $patchNames += 'constant-reuse' }
$gpuTiming = $Variant -in @('gpu', 'optimized-gpu')
if ($gpuTiming) { $patchNames += 'gpu-timing' }
$patches = @($patchNames | ForEach-Object {
    (Resolve-Path -LiteralPath (Join-Path $root "sdk-patches/rexglue-0.10.0/$_.patch")).Path
})
$stageName = if ($Variant -in @('baseline', 'batched')) { 'renderer-sdk-source' } else { "renderer-sdk-$Variant" }
$stage = Join-Path $root "out/$stageName"
if (-not (Test-Path -LiteralPath $stage)) {
    & git -C $SourcePath worktree add --detach $stage $revision
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the isolated SDK source worktree.' }
}
if ((& git -C $stage rev-parse HEAD) -ne $revision -or $LASTEXITCODE -ne 0) {
    throw 'The isolated SDK source has changed revision; refusing to modify it.'
}
# Construct the exact expected combined diff in a temporary Git index, without
# touching a developer checkout. This also covers new profiler source files.
$priorIndex = $env:GIT_INDEX_FILE
$verifyIndex = Join-Path ([System.IO.Path]::GetTempPath()) ("crackdown-renderer-" + [guid]::NewGuid().ToString('N') + '.index')
try {
    $env:GIT_INDEX_FILE = $verifyIndex
    & git -C $stage read-tree HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the patch verification index.' }
    foreach ($patch in $patches) {
        & git -C $stage apply --cached $patch
        if ($LASTEXITCODE -ne 0) { throw "Renderer patches do not compose cleanly: $patch" }
    }
    $expectedPatch = ((& git -C $stage diff --no-ext-diff --binary --cached HEAD --) -join "`n").TrimEnd()
    if ($LASTEXITCODE -ne 0) { throw 'Could not calculate the expected renderer changes.' }
} finally {
    $env:GIT_INDEX_FILE = $priorIndex
    if (Test-Path -LiteralPath $verifyIndex) { Remove-Item -LiteralPath $verifyIndex }
}
$untracked = & git -C $stage ls-files --others
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect untracked files in the isolated SDK.' }
if ($untracked) {
    throw 'The isolated SDK contains untracked files; refusing to build a mixed variant.'
}
& git -C $stage diff --quiet HEAD --
if ($LASTEXITCODE -eq 0) {
    foreach ($patch in $patches) {
        & git -C $stage apply --index $patch
        if ($LASTEXITCODE -ne 0) { throw "Could not apply the renderer patch: $patch" }
    }
} elseif ($LASTEXITCODE -ne 1) {
    throw 'Could not check the isolated renderer source.'
}
# A reverse-applicable hunk alone would also accept unrelated source/header
# edits. Require the complete source diff to match this variant's patch list.
$actualPatch = ((& git -C $stage diff --no-ext-diff --binary HEAD --) -join "`n").TrimEnd()
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the isolated SDK source.' }
$untracked = & git -C $stage ls-files --others
if ($LASTEXITCODE -ne 0) { throw 'Could not verify untracked files in the isolated SDK.' }
if ($actualPatch -cne $expectedPatch -or $untracked) {
    throw 'The isolated SDK does not match the selected patches; refusing to build a mixed variant.'
}
$variantName = "renderer-$Variant"
$build = Join-Path $root "out/build/$variantName"
[void][System.IO.Directory]::CreateDirectory($build)
$commandProcessor = Join-Path $stage 'src/graphics/command_processor.cpp'
if ($Baseline) {
    $commandProcessor = Join-Path $build 'command_processor_baseline.cpp'
    $original = & git -C $stage show 'HEAD:src/graphics/command_processor.cpp'
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the unpatched command processor.' }
    [System.IO.File]::WriteAllLines($commandProcessor, $original, [System.Text.UTF8Encoding]::new($false))
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ Build Tools are required.' }
$vcvars = Join-Path $vs 'VC/Auxiliary/Build/vcvars64.bat'
$devEnvironment = & cmd.exe /d /c ('"{0}" >nul && set' -f $vcvars)
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the Visual Studio build environment.' }
foreach ($line in $devEnvironment) {
    if ($line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$cmakeBin = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin'
$clangBin = Join-Path $vs 'VC/Tools/Llvm/x64/bin'
$ninjaBin = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja'
$env:PATH = "$clangBin;$ninjaBin;$cmakeBin;$SdkPath/bin;$env:PATH"
& python (Join-Path $root 'tests/renderer_type0_test.py') --source $stage --sdk $SdkPath `
    --cxx (Join-Path $clangBin 'clang++.exe') --output (Join-Path $root 'out/renderer-type0-regression')
if ($LASTEXITCODE -ne 0) { throw 'Renderer register regression checks failed.' }
foreach ($suite in @('ranges', 'constants')) {
    if (($suite -eq 'ranges' -and $patchNames -contains 'single-range-allocation') -or
        ($suite -eq 'constants' -and $patchNames -contains 'constant-packing')) {
        & python (Join-Path $root "tests/renderer_${suite}_test.py") --source $stage --sdk $SdkPath `
            --cxx (Join-Path $clangBin 'clang++.exe') --output (Join-Path $root "out/renderer-$suite-regression")
        if ($LASTEXITCODE -ne 0) { throw "Renderer $suite regression checks failed." }
    }
}
$additionalSuites = @{
    'control-register-batching' = 'renderer_control_test.py'
    'command-stream-reuse' = 'renderer_command_stream_test.py'
    'constant-reuse' = 'renderer_constant_reuse_test.py'
}
foreach ($patchName in $patchNames) {
    if ($additionalSuites.ContainsKey($patchName)) {
        & python (Join-Path $root "tests/$($additionalSuites[$patchName])") --source $stage --sdk $SdkPath `
            --cxx (Join-Path $clangBin 'clang++.exe')
        if ($LASTEXITCODE -ne 0) { throw "Renderer $patchName regression checks failed." }
    }
}
$timingOption = if ($gpuTiming) { 'ON' } else { 'OFF' }
if ($gpuTiming) {
    & python (Join-Path $root 'tests/gpu_timing_test.py') --source $stage --cxx (Join-Path $clangBin 'clang++.exe')
    if ($LASTEXITCODE -ne 0) { throw 'GPU timestamp lifecycle checks failed.' }
    & python (Join-Path $root 'tests/summarize_gpu_timing_test.py')
    if ($LASTEXITCODE -ne 0) { throw 'GPU timing summary checks failed.' }
}
& cmake -S (Join-Path $PSScriptRoot 'renderer') -B $build -G Ninja `
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=clang++ "-DCMAKE_PREFIX_PATH=$SdkPath" `
    "-DREX_SOURCE=$stage" "-DCOMMAND_PROCESSOR_SOURCE=$commandProcessor" "-DRENDERER_GPU_TIMING=$timingOption"
if ($LASTEXITCODE -ne 0) { throw 'Renderer configuration failed.' }
& cmake --build $build --parallel $Jobs
if ($LASTEXITCODE -ne 0) { throw 'Renderer build failed.' }
if ($PluginOnly) {
    Write-Output (Join-Path $build 'rexgpu-xenos.dll')
    exit 0
}
$gameBuild = Join-Path $root 'out/build/win-amd64-experiments'
$game = Join-Path $gameBuild 'crackdown-performance.exe'
if (-not (Test-Path -LiteralPath $game -PathType Leaf)) {
    throw 'Plugin built. Run build-local.ps1 -PerformanceTest, then rerun this script to stage a playable variant.'
}
if ((Get-FileHash "$gameBuild/rexruntime.dll").Hash -ne (Get-FileHash "$SdkPath/bin/rexruntime.dll").Hash) {
    throw 'The game runtime does not match the SDK used for this renderer.'
}
$destination = Join-Path $root "out/variants/$variantName"
$executable = Join-Path $destination 'crackdown-renderer.exe'
if (Get-CimInstance Win32_Process -Filter "Name = 'crackdown-renderer.exe'" | Where-Object { $_.ExecutablePath -eq $executable }) {
    throw "Close the running $variantName instance before staging this build."
}
[void][System.IO.Directory]::CreateDirectory($destination)
Copy-Item -LiteralPath $game -Destination $executable
Copy-Item -LiteralPath "$gameBuild/rexruntime.dll" -Destination $destination
foreach ($file in @('rexgpu-xenos.dll', 'rexgpu-xenos.pdb', 'rexgpu-xenos.map')) {
    Copy-Item -LiteralPath (Join-Path $build $file) -Destination $destination
}
$gamePdb = Join-Path $gameBuild 'crackdown-performance.pdb'
if (Test-Path -LiteralPath $gamePdb) { Copy-Item -LiteralPath $gamePdb -Destination $destination }
$metadata = (& (Join-Path $build 'renderer_offsets.exe') | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Could not generate renderer profiling offsets.' }
$hashes = @{}
foreach ($file in @('rexruntime.dll', 'rexgpu-xenos.dll')) {
    $hashes[$file] = (Get-FileHash -LiteralPath (Join-Path $destination $file) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$metadata | Add-Member -NotePropertyName dll_sha256 -NotePropertyValue $hashes
$metadata | Add-Member -NotePropertyName renderer_variant -NotePropertyValue $Variant
$metadata | Add-Member -NotePropertyName renderer_patches -NotePropertyValue $patchNames
[System.IO.File]::WriteAllText((Join-Path $destination 'offsets.json'),
    ($metadata | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
Write-Output $executable
} finally {
    $env:GIT_INDEX_FILE = $originalGitIndex
}
