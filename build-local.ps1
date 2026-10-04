param(
    [string]$SdkPath = $env:REXGLUE_SDK_ROOT,
    [switch]$Regenerate,
    [switch]$TestOnly,
    [switch]$Experiments,
    [ValidateRange(1, 64)][int]$Jobs = 8
)
$ErrorActionPreference = 'Stop'
if (-not $SdkPath) { $SdkPath = Join-Path $PSScriptRoot '../.tools/rexglue-v0.10.0/win-amd64' }
$SdkPath = (Resolve-Path -LiteralPath $SdkPath).Path
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
$cmake = Join-Path $cmakeBin 'cmake.exe'
Push-Location $PSScriptRoot
try {
    # Regenerate incrementally so manifest or SDK changes cannot reuse stale code.
    $codegenArgs = @('codegen', 'crackdown_config.toml')
    if ($Regenerate) { $codegenArgs += '--ignore-stamp' }
    & "$SdkPath/bin/rexglue.exe" @codegenArgs
    if ($LASTEXITCODE -ne 0) { throw 'ReXGlue code generation failed.' }
    $buildDirectory = Join-Path $PSScriptRoot 'out/build/win-amd64-release'
    if ($Experiments) { $buildDirectory = Join-Path $PSScriptRoot 'out/build/win-amd64-experiments' }
    $experimentOption = if ($Experiments) { 'ON' } else { 'OFF' }
    & $cmake --preset win-amd64-release -B $buildDirectory "-DCMAKE_PREFIX_PATH=$SdkPath" -DCRACKDOWN_BUILD_TESTS=ON "-DCRACKDOWN_BUILD_EXPERIMENTS=$experimentOption"
    if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
    & $cmake --build $buildDirectory --target crackdown_check --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { throw 'Regression checks failed.' }
    if (-not $TestOnly) {
        & $cmake --build $buildDirectory --parallel $Jobs
        if ($LASTEXITCODE -ne 0) { throw 'Crackdown build failed.' }
    }
} finally { Pop-Location }
