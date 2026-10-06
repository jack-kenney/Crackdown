param(
    [ValidateSet(0, 60, 120, 144, 240)][int]$FrameRate = 120,
    [switch]$Baseline,
    [switch]$Automation,
    [switch]$Windowed,
    [string]$ProfileSource,
    [switch]$PrintArguments
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'launcher-settings.ps1')
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$executable = Join-Path $root 'out/variants/object-timing/crackdown-physics-trace.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf) -or -not (Get-OptimizedBuild $executable)) {
    throw 'Build the isolated test with tools/build-physics-trace.ps1 -ObjectTimingVariant.'
}
$settings = Read-LauncherSettings (Join-Path $root 'out/launcher-settings.json')
if ($Windowed) { $settings.fullscreen = $false }
$session = Join-Path $root ('out/object-timing/session-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-ffff'))
$profile = Join-Path $session 'userdata'
if (-not $ProfileSource) {
    foreach ($candidate in @('userdata-renderer-test', 'userdata-physics-trace', 'userdata-performance-test', 'userdata')) {
        $source = Join-Path $root "out/$candidate"
        if (Test-Path -LiteralPath $source -PathType Container) { $ProfileSource = $source; break }
    }
}
if (-not $ProfileSource -or -not (Test-Path -LiteralPath $ProfileSource -PathType Container)) {
    throw 'An existing save profile is required; use -ProfileSource.'
}
$ProfileSource = (Resolve-Path -LiteralPath $ProfileSource).Path
$arguments = @(Get-GameArguments $root $settings -IgnoreBuildProfile | Where-Object {
    $_ -notmatch '^--(user_data_root|log_file|native_frame_rate|automation|skip_intro_movies|normalize_lift_fade|lift_fade_trace_path|normalize_garage_door_timer|garage_door_trace_path)='
})
$arguments += "--user_data_root=$profile", "--log_file=$session/game.log",
    "--native_frame_rate=$FrameRate", "--normalize_lift_fade=$(((-not $Baseline).ToString()).ToLowerInvariant())",
    "--lift_fade_trace_path=$session/fade.csv", '--skip_intro_movies=true',
    "--normalize_garage_door_timer=$(((-not $Baseline).ToString()).ToLowerInvariant())",
    "--garage_door_trace_path=$session/garage-door.csv",
    '--native_discard_hitch_time=true', '--fix_guest_event_clear=true', '--pace_gpu_wait=true',
    "--automation=$($Automation.IsPresent.ToString().ToLowerInvariant())"
if ($PrintArguments) {
    [pscustomobject]@{ executable=$executable; arguments=$arguments; profileSource=$ProfileSource } | ConvertTo-Json
    return
}
[void][IO.Directory]::CreateDirectory($session)
Get-ChildItem -LiteralPath $ProfileSource -Force | Where-Object { $_.Name -ne 'cache' } |
    Copy-Item -Destination ([IO.Directory]::CreateDirectory($profile).FullName) -Recurse
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName=$executable; $start.WorkingDirectory=$root
$start.UseShellExecute=$false; $start.CreateNoWindow=$true
$start.Arguments=($arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
$game=[Diagnostics.Process]::Start($start)
[IO.File]::WriteAllText((Join-Path $session 'launch.json'), (@{
    executable=$executable; arguments=$arguments; profileSource=$ProfileSource; pid=$game.Id
} | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
[pscustomobject]@{ GameProcessId=$game.Id; RunDirectory=$session; Mode=$(if ($Baseline) {'object-baseline'} else {'object-normalized'}) }
