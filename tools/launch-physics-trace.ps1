param(
    [ValidateSet(0, 60, 120, 144, 240)][int]$FrameRate = 240,
    [switch]$Automation,
    [switch]$Windowed,
    [switch]$NormalizeStepDown,
    [switch]$PrintArguments
)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'launcher-settings.ps1')
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $variant = if ($NormalizeStepDown) { 'physics-step-down' } else { 'physics-trace' }
    $executable = Join-Path $root "out/variants/$variant/crackdown-physics-trace.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'Build the diagnostic executable with tools/build-physics-trace.ps1.'
    }
    if (-not (Get-OptimizedBuild $executable)) { throw 'Missing diagnostic build descriptor. Rebuild tools/build-physics-trace.ps1.' }
    $settings = Read-LauncherSettings (Join-Path $root 'out/launcher-settings.json')
    if ($Windowed) { $settings.fullscreen = $false }
    $profile = Join-Path $root 'out/userdata-physics-trace'
    $session = Join-Path $root ('out/performance/physics-trace-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff'))
    $arguments = @(Get-GameArguments $root $settings -IgnoreBuildProfile | Where-Object {
        -not $_.StartsWith('--user_data_root=') -and -not $_.StartsWith('--log_file=')
    })
    $arguments += "--user_data_root=$profile"
    $arguments += "--log_file=$(Join-Path $session 'game.log')"
    $arguments += "--physics_trace_path=$(Join-Path $session 'controllers.csv')"
    $arguments += "--native_frame_rate=$FrameRate"
    $arguments += '--native_discard_hitch_time=true'
    $arguments += '--fix_guest_event_clear=true'
    $arguments += '--pace_gpu_wait=true'
    $arguments += '--vehicle_lod1_distance=15'
    $arguments += "--automation=$($Automation.IsPresent.ToString().ToLowerInvariant())"
    $arguments += "--normalize_character_step_down=$($NormalizeStepDown.IsPresent.ToString().ToLowerInvariant())"
    if ($PrintArguments) {
        [pscustomobject]@{ executable=$executable; arguments=$arguments; session=$session } | ConvertTo-Json
        exit 0
    }
    if (Get-CimInstance Win32_Process -Filter "Name = 'crackdown-physics-trace.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($profile, [StringComparison]::OrdinalIgnoreCase) -ge 0 }) {
        throw 'Close the existing physics diagnostic game before launching another with this profile.'
    }
    Assert-LauncherProfileAvailable $profile
    if (-not (Test-Path -LiteralPath $profile)) {
        [void][IO.Directory]::CreateDirectory($profile)
        foreach ($candidate in @('userdata-renderer-test', 'userdata-performance-test', 'userdata')) {
            $source = Join-Path $root "out/$candidate"
            if (-not (Test-Path -LiteralPath $source -PathType Container)) { continue }
            Get-ChildItem -LiteralPath $source -Force | Where-Object { $_.Name -ne 'cache' } |
                Copy-Item -Destination $profile -Recurse
            break
        }
    }
    [void][IO.Directory]::CreateDirectory($session)
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $executable
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.Arguments = ($arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
    $process = [Diagnostics.Process]::Start($start)
    [IO.File]::WriteAllText((Join-Path $session 'launch.json'),
        ([pscustomobject]@{ pid=$process.Id; startUtc=[DateTime]::UtcNow.ToString('o'); executable=$executable; arguments=$arguments } | ConvertTo-Json),
        [Text.UTF8Encoding]::new($false))
    Write-Output "Physics diagnostic PID $($process.Id): $session"
} catch {
    if ($PrintArguments) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Crackdown physics diagnostic')
    exit 1
}
