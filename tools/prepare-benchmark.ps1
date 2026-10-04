param([string]$SdkPath = $env:REXGLUE_SDK_ROOT)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $SdkPath) { $SdkPath = Join-Path $root '../.tools/rexglue-v0.10.0/win-amd64' }
$SdkPath = (Resolve-Path -LiteralPath $SdkPath).Path
$output = Join-Path $root 'out/benchmark'
[void][IO.Directory]::CreateDirectory($output)
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ Build Tools are required to prepare frame sampling.' }
$vcvars = Join-Path $vs 'VC/Auxiliary/Build/vcvars64.bat'
$devEnvironment = & cmd.exe /d /c ('"{0}" >nul && set' -f $vcvars)
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the build environment.' }
foreach ($line in $devEnvironment) {
    if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$clang = Join-Path $vs 'VC/Tools/Llvm/x64/bin/clang++.exe'
$helper = Join-Path $output 'offsets.exe'
& $clang -std=c++23 -O2 -fno-access-control -Wno-invalid-offsetof -DNDEBUG `
    -DSPDLOG_FMT_EXTERNAL -DSPDLOG_COMPILED_LIB -D_DLL -D_MT -Xclang --dependent-lib=msvcrt `
    -isystem "$SdkPath/include" -isystem "$SdkPath/include/dxc" -isystem "$SdkPath/include/renderdoc" `
    "$PSScriptRoot/benchmark_offsets.cpp" -o $helper -fuse-ld=lld-link
if ($LASTEXITCODE -ne 0) { throw 'Benchmark metadata helper build failed.' }
$metadata = (& $helper | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Benchmark metadata helper failed.' }
$hashes = @{}
foreach ($dll in @('rexruntime.dll', 'rexgpu-xenos.dll')) {
    $hashes[$dll] = (Get-FileHash -LiteralPath "$SdkPath/bin/$dll" -Algorithm SHA256).Hash.ToLowerInvariant()
}
$metadata | Add-Member -NotePropertyName dll_sha256 -NotePropertyValue $hashes
$path = Join-Path $output 'offsets.json'
[IO.File]::WriteAllText($path, ($metadata | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Write-Output "Frame sampling metadata: $path"
