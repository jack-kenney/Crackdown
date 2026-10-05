param([ValidateRange(1, 64)][int]$Jobs = 2, [switch]$StepDownVariant)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$variant = if ($StepDownVariant) { 'physics-step-down' } else { 'physics-trace' }
$destination = Join-Path $root "out/variants/$variant"
$executable = Join-Path $destination 'crackdown-physics-trace.exe'
if (Get-CimInstance Win32_Process -Filter "Name = 'crackdown-physics-trace.exe'" |
    Where-Object { $_.ExecutablePath -eq $executable }) {
    throw 'Close the diagnostic game before rebuilding its executable.'
}
& (Join-Path $root 'build-local.ps1') -PerformanceTest -Jobs $Jobs
$renderer = Join-Path $root 'out/variants/renderer-frontend'
if (-not (Test-Path -LiteralPath (Join-Path $renderer 'offsets.json'))) {
    & (Join-Path $PSScriptRoot 'build-renderer.ps1') -Variant frontend -Jobs $Jobs
}
$offsets = Get-Content -LiteralPath (Join-Path $renderer 'offsets.json') -Raw | ConvertFrom-Json
foreach ($dll in @('rexruntime.dll', 'rexgpu-xenos.dll')) {
    if ((Get-FileHash -LiteralPath (Join-Path $renderer $dll) -Algorithm SHA256).Hash -ne $offsets.dll_sha256.$dll) {
        throw 'Renderer DLL hashes do not match. Rebuild tools/build-renderer.ps1 -Variant frontend.'
    }
}
[void][IO.Directory]::CreateDirectory($destination)
Copy-Item -LiteralPath (Join-Path $root 'out/build/win-amd64-experiments/crackdown-performance.exe') -Destination $executable
foreach ($file in @('rexruntime.dll', 'rexgpu-xenos.dll', 'offsets.json')) {
    Copy-Item -LiteralPath (Join-Path $renderer $file) -Destination $destination
}
Copy-Item -LiteralPath (Join-Path $root 'out/build/win-amd64-experiments/crackdown-performance.pdb') -Destination $destination
$descriptor = @{
    schemaVersion = 1; executableName = 'crackdown-physics-trace.exe'; rendererVariant = 'frontend'
    executableSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    dllSha256 = $offsets.dll_sha256
}
[IO.File]::WriteAllText((Join-Path $destination 'optimized-build.json'),
    ($descriptor | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Write-Output "Physics diagnostic ready: $executable"
