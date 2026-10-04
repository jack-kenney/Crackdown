param(
    [string]$ProfileSource,
    [ValidateSet(1, 2, 3)][int]$ResolutionScale,
    [ValidateSet('none', 'fxaa', 'fxaa_extreme')][string]$Antialiasing,
    [ValidateSet(-1, 0, 1, 2, 3, 4, 5)][int]$AnisotropicOverride,
    [bool]$FixLighting,
    [bool]$Bloom,
    [bool]$Shadows,
    [bool]$Fullscreen = $false
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. "$PSScriptRoot/launcher-settings.ps1"
$settings = Read-LauncherSettings (Join-Path $root 'out/launcher-settings.json')
$settings.fullscreen = $Fullscreen
foreach ($pair in @(@('ResolutionScale', 'resolutionScale'), @('Antialiasing', 'antialiasing'),
                    @('AnisotropicOverride', 'anisotropicOverride'),
                    @('FixLighting', 'fixLighting'), @('Bloom', 'bloom'), @('Shadows', 'shadows'))) {
    if ($PSBoundParameters.ContainsKey($pair[0])) { $settings[$pair[1]] = $PSBoundParameters[$pair[0]] }
}
$exe = $settings.executablePath
if (-not $exe) { $exe = Join-Path $root 'out/build/win-amd64-release/crackdown.exe' }
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Game executable missing: $exe" }
if (-not $ProfileSource) { $ProfileSource = Join-Path $root 'out/benchmark/profile-baseline' }
if (-not (Test-Path -LiteralPath $ProfileSource -PathType Container)) {
    throw "Benchmark save baseline missing: $ProfileSource. Copy out/userdata there while the game is closed, or provide -ProfileSource."
}
$ProfileSource = (Resolve-Path -LiteralPath $ProfileSource).Path
$run = Join-Path $root ('out/benchmark/session-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-ffff'))
[void][IO.Directory]::CreateDirectory($run)
$userdata = Join-Path $run 'userdata'
Copy-Item -LiteralPath $ProfileSource -Destination $userdata -Recurse
$arguments = @(Get-GameArguments $root $settings | Where-Object { $_ -notmatch '^--(user_data_root|log_file)=' })
$arguments += "--user_data_root=$userdata", "--log_file=$run/game.log", '--automation=true'
[IO.File]::WriteAllText((Join-Path $run 'launch.json'), (@{
    executable = $exe; arguments = $arguments; profileSource = $ProfileSource; settings = $settings
} | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
$quoted = ($arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
$game = Start-Process -FilePath $exe -ArgumentList $quoted -WorkingDirectory $root -PassThru
Write-Host "Benchmark PID: $($game.Id)"
Write-Host "Separate save/profile and logs: $run"
Write-Host "Load and position using: python tools/benchmark_game.py live --pid $($game.Id)"
[pscustomobject]@{ GameProcessId = $game.Id; RunDirectory = $run; UserData = $userdata }
