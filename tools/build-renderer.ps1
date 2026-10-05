param(
    [string]$SdkPath = $env:REXGLUE_SDK_ROOT,
    [string]$SourcePath,
    [switch]$Baseline,
    [switch]$PluginOnly,
    [ValidateRange(1, 64)][int]$Jobs = 4
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $SdkPath) { $SdkPath = Join-Path $root '../.tools/rexglue-v0.10.0/win-amd64' }
if (-not $SourcePath) { $SourcePath = Join-Path $root '../.tools/rexglue-source-v0.10.0' }
$SdkPath = (Resolve-Path -LiteralPath $SdkPath).Path
$SourcePath = (Resolve-Path -LiteralPath $SourcePath).Path
$revision = 'f5337cdc947ff6d4c4196737e2c807a48f2a1fc2'
if ((& git -C $SourcePath rev-parse HEAD) -ne $revision -or $LASTEXITCODE -ne 0) {
    throw 'The renderer experiment requires the exact ReXGlue v0.10.0 source revision.'
}
$stage = Join-Path $root 'out/renderer-sdk-source'
if (-not (Test-Path -LiteralPath $stage)) {
    & git -C $SourcePath worktree add --detach $stage $revision
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the isolated SDK source worktree.' }
}
if ((& git -C $stage rev-parse HEAD) -ne $revision -or $LASTEXITCODE -ne 0) {
    throw 'The isolated SDK source has changed revision; refusing to modify it.'
}
$patch = Join-Path $root 'sdk-patches/rexglue-0.10.0/type0-register-batching.patch'
& git -C $stage diff --quiet HEAD -- src/graphics/command_processor.cpp
if ($LASTEXITCODE -eq 0) {
    & git -C $stage apply --check $patch
    if ($LASTEXITCODE -ne 0) { throw 'The renderer patch conflicts with the isolated source.' }
    & git -C $stage apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply the renderer patch.' }
} else {
    & git -C $stage apply --reverse --check $patch
    if ($LASTEXITCODE -ne 0) { throw 'The isolated renderer source does not match the expected patch.' }
}
# A reverse-applicable hunk alone would also accept unrelated source/header
# edits. Keep both builds pinned to exactly this patch and the released ABI.
$actualPatch = ((& git -C $stage diff --no-ext-diff --binary HEAD --) -join "`n").TrimEnd()
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the isolated SDK source.' }
$expectedPatch = [System.IO.File]::ReadAllText($patch).Replace("`r`n", "`n").TrimEnd()
if ($actualPatch -cne $expectedPatch -or (& git -C $stage ls-files --others)) {
    throw 'The isolated SDK contains changes beyond the renderer patch; refusing to build a mixed variant.'
}
$variant = if ($Baseline) { 'renderer-baseline' } else { 'renderer-batched' }
$build = Join-Path $root "out/build/$variant"
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
& cmake -S (Join-Path $PSScriptRoot 'renderer') -B $build -G Ninja `
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=clang++ "-DCMAKE_PREFIX_PATH=$SdkPath" `
    "-DREX_SOURCE=$stage" "-DCOMMAND_PROCESSOR_SOURCE=$commandProcessor"
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
$destination = Join-Path $root "out/variants/$variant"
$executable = Join-Path $destination 'crackdown-renderer.exe'
if (Get-CimInstance Win32_Process -Filter "Name = 'crackdown-renderer.exe'" | Where-Object { $_.ExecutablePath -eq $executable }) {
    throw "Close the running $variant instance before staging this build."
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
[System.IO.File]::WriteAllText((Join-Path $destination 'offsets.json'),
    ($metadata | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
Write-Output $executable
