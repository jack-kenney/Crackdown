param(
    [string]$SdkPath = $env:REXGLUE_SDK_ROOT,
    [switch]$Regenerate,
    [switch]$TestOnly,
    [switch]$Experiments,
    [switch]$PerformanceTest,
    [switch]$Optimized,
    [string]$RendererSourcePath,
    [ValidateRange(1, 64)][int]$Jobs = 8
)
$ErrorActionPreference = 'Stop'
if ($Optimized) { $PerformanceTest = $true }
if ($PerformanceTest) { $Experiments = $true }
$launcherBuild = Join-Path $PSScriptRoot 'out/build/win-amd64-release'
$launcherExecutable = Join-Path $launcherBuild 'crackdown.exe'
function Assert-LauncherBuildStopped {
    if (Get-CimInstance Win32_Process -Filter "Name = 'crackdown.exe'" |
        Where-Object { $_.ExecutablePath -eq $launcherExecutable }) {
        throw 'Close the game launched from the standard build before replacing it.'
    }
}
if (-not $TestOnly -and ($Optimized -or -not $Experiments)) { Assert-LauncherBuildStopped }
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
    $executableName = if ($PerformanceTest) { 'crackdown-performance' } else { 'crackdown' }
    & $cmake --preset win-amd64-release -B $buildDirectory "-DCMAKE_PREFIX_PATH=$SdkPath" -DCRACKDOWN_BUILD_TESTS=ON "-DCRACKDOWN_BUILD_EXPERIMENTS=$experimentOption" "-DCRACKDOWN_EXECUTABLE_NAME=$executableName"
    if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
    & $cmake --build $buildDirectory --target crackdown_check --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { throw 'Regression checks failed.' }
    if (-not $TestOnly) {
        if (-not $Experiments -and (Test-Path -LiteralPath (Join-Path $launcherBuild 'optimized-build.json'))) {
            # Staging the optimized image makes it newer than the stock objects.
            # Remove that owned output so Ninja must relink the ordinary game
            # and run its SDK DLL staging, rather than accepting the copied exe.
            if (Test-Path -LiteralPath $launcherExecutable) { Remove-Item -LiteralPath $launcherExecutable }
        }
        & $cmake --build $buildDirectory --parallel $Jobs
        if ($LASTEXITCODE -ne 0) { throw 'Crackdown build failed.' }
        if ($Optimized) {
            $rendererArguments = @{ Variant = 'frontend'; SdkPath = $SdkPath; Jobs = $Jobs }
            if ($RendererSourcePath) { $rendererArguments.SourcePath = $RendererSourcePath }
            & (Join-Path $PSScriptRoot 'tools/build-renderer.ps1') @rendererArguments
            $variant = Join-Path $PSScriptRoot 'out/variants/renderer-frontend'
            Assert-LauncherBuildStopped
            [void][IO.Directory]::CreateDirectory($launcherBuild)
            Copy-Item -LiteralPath (Join-Path $variant 'crackdown-renderer.exe') -Destination $launcherExecutable
            foreach ($file in @('rexruntime.dll', 'rexgpu-xenos.dll', 'rexgpu-xenos.pdb', 'rexgpu-xenos.map', 'offsets.json', 'crackdown-performance.pdb')) {
                Copy-Item -LiteralPath (Join-Path $variant $file) -Destination $launcherBuild
            }
            # A hash-paired descriptor tells the settings launcher which timing
            # flags and existing save profile belong to this staged build.
            $offsets = Get-Content -LiteralPath (Join-Path $variant 'offsets.json') -Raw | ConvertFrom-Json
            $descriptor = @{
                schemaVersion = 1; executableName = 'crackdown.exe'; rendererVariant = 'frontend'
                executableSha256 = (Get-FileHash -LiteralPath $launcherExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
                dllSha256 = $offsets.dll_sha256
            }
            [IO.File]::WriteAllText((Join-Path $launcherBuild 'optimized-build.json'),
                ($descriptor | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
            $profile = Join-Path $PSScriptRoot 'out/userdata-renderer-test'
            if (-not (Test-Path -LiteralPath $profile)) {
                foreach ($candidate in @('userdata-performance-test', 'userdata-fps-experimental', 'userdata')) {
                    $sourceProfile = Join-Path $PSScriptRoot "out/$candidate"
                    if (Test-Path -LiteralPath $sourceProfile -PathType Container) {
                        [void][IO.Directory]::CreateDirectory($profile)
                        Get-ChildItem -LiteralPath $sourceProfile -Force | Where-Object { $_.Name -ne 'cache' } |
                            Copy-Item -Destination $profile -Recurse
                        break
                    }
                }
            }
            Write-Output "Optimized build ready for Launch-Crackdown.cmd: $launcherExecutable"
        } elseif (-not $Experiments) {
            $descriptorPath = Join-Path $launcherBuild 'optimized-build.json'
            if (Test-Path -LiteralPath $descriptorPath) { Remove-Item -LiteralPath $descriptorPath }
        }
    }
} finally { Pop-Location }
