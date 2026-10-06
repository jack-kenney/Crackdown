param(
    [ValidateSet('off', 'depth', 'motion', 'history', 'capture')][string]$Mode = 'off',
    [ValidateSet(60, 120, 144, 240)][int]$FrameRate = 120,
    [string]$ProfileSource,
    [switch]$Automation,
    [switch]$Trace,
    [switch]$DebugLayer,
    [switch]$PrintArguments
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'launcher-settings.ps1')
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe = Join-Path $root 'out/variants/renderer-temporal/crackdown-renderer.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw 'Build the experiment with tools/build-renderer.ps1 -Variant temporal.'
}
$settings = Read-LauncherSettings (Join-Path $root 'out/launcher-settings.json')
$settings.fullscreen = $false
$session = Join-Path $root ('out/temporal/session-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-ffff'))
$profile = Join-Path $session 'userdata'
if (-not $ProfileSource) { $ProfileSource = Join-Path $root 'out/userdata-renderer-test' }
if (-not (Test-Path -LiteralPath $ProfileSource -PathType Container)) {
    throw "Save profile missing: $ProfileSource. Supply -ProfileSource."
}
$ProfileSource = (Resolve-Path -LiteralPath $ProfileSource).Path
$arguments = @(Get-GameArguments $root $settings -IgnoreBuildProfile | Where-Object {
    $_ -notmatch '^--(user_data_root|log_file|native_frame_rate|automation|skip_intro_movies|temporal_mode)='
})
$modes = @{ off=0; depth=1; motion=2; history=3; capture=4 }
$arguments += "--user_data_root=$profile", "--log_file=$session/game.log",
    "--native_frame_rate=$FrameRate", "--temporal_mode=$($modes[$Mode])", '--skip_intro_movies=true',
    '--native_discard_hitch_time=true', '--fix_guest_event_clear=true', '--pace_gpu_wait=true',
    "--automation=$($Automation.IsPresent.ToString().ToLowerInvariant())"
if ($Trace) { $arguments += "--temporal_trace_path=$session/depth-trace.csv" }
if ($DebugLayer) { $arguments += '--d3d12_debug=true' }
if ($PrintArguments) {
    [pscustomobject]@{ executable=$exe; arguments=$arguments; profileSource=$ProfileSource } | ConvertTo-Json
    return
}
[void][IO.Directory]::CreateDirectory($session)
Get-ChildItem -LiteralPath $ProfileSource -Force | Where-Object { $_.Name -ne 'cache' } |
    Copy-Item -Destination ([IO.Directory]::CreateDirectory($profile).FullName) -Recurse
[IO.File]::WriteAllText((Join-Path $session 'launch.json'), (@{
    executable=$exe; arguments=$arguments; profileSource=$ProfileSource
} | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName=$exe; $start.WorkingDirectory=$root
$start.UseShellExecute=$false; $start.CreateNoWindow=$true
$start.Arguments=($arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
$game=[Diagnostics.Process]::Start($start)
[pscustomobject]@{ GameProcessId=$game.Id; RunDirectory=$session; Mode=$Mode }
