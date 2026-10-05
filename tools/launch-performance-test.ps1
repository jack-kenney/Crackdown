param(
    [ValidateSet(60, 120, 144, 240)][int]$FrameRate = 60,
    [ValidateRange(0, 100)][double]$VehicleLod1Distance = 0,
    [ValidateSet('stock', 'baseline', 'batched')][string]$Renderer = 'stock',
    [switch]$PrintArguments
)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'launcher-settings.ps1')
    $root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $executable = Join-Path $root 'out/build/win-amd64-experiments/crackdown-performance.exe'
    if ($Renderer -ne 'stock') {
        $executable = Join-Path $root "out/variants/renderer-$Renderer/crackdown-renderer.exe"
    }
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'Build the performance executable with build-local.ps1 -PerformanceTest; renderer variants also need tools/build-renderer.ps1 (or -Baseline).'
    }
    $settings = Read-LauncherSettings (Join-Path $root 'out/launcher-settings.json')
    $profile = Join-Path $root 'out/userdata-performance-test'
    $logName = 'performance-test-game.log'
    if ($Renderer -ne 'stock') {
        $profile = Join-Path $root 'out/userdata-renderer-test'
        $logName = "renderer-$Renderer-game.log"
    }
    $arguments = @(Get-GameArguments $root $settings | Where-Object {
        -not $_.StartsWith('--user_data_root=') -and -not $_.StartsWith('--log_file=')
    })
    $arguments += "--user_data_root=$profile"
    $arguments += "--log_file=$(Join-Path $root "out/$logName")"
    $arguments += "--native_frame_rate=$FrameRate"
    $arguments += '--native_discard_hitch_time=true'
    $arguments += '--fix_guest_event_clear=true'
    $arguments += '--pace_gpu_wait=true'
    $arguments += "--vehicle_lod1_distance=$($VehicleLod1Distance.ToString([System.Globalization.CultureInfo]::InvariantCulture))"
    if ($PrintArguments) {
        [pscustomobject]@{ executable = $executable; arguments = $arguments } | ConvertTo-Json
        exit 0
    }
    if ($Renderer -ne 'stock' -and (Get-CimInstance Win32_Process -Filter "Name = 'crackdown-renderer.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($profile, [StringComparison]::OrdinalIgnoreCase) -ge 0 })) {
        throw 'Close the existing renderer comparison instance before starting another with the same save profile.'
    }
    # Preserve the current experimental progress in a separate profile, once.
    if (-not (Test-Path -LiteralPath $profile)) {
        $sourceProfile = Join-Path $root 'out/userdata-fps-experimental'
        if ($Renderer -ne 'stock') { $sourceProfile = Join-Path $root 'out/userdata-performance-test' }
        if (-not (Test-Path -LiteralPath $sourceProfile -PathType Container)) {
            $sourceProfile = Join-Path $root 'out/userdata'
        }
        if (Test-Path -LiteralPath $sourceProfile -PathType Container) {
            [void][System.IO.Directory]::CreateDirectory($profile)
            # Shader caches may be locked by a running instance; copy progress
            # and let the independent renderer rebuild its disposable cache.
            Get-ChildItem -LiteralPath $sourceProfile -Force | Where-Object { $_.Name -ne 'cache' } |
                Copy-Item -Destination $profile -Recurse
        } else {
            [void][System.IO.Directory]::CreateDirectory($profile)
        }
    }
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $executable
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.Arguments = ($arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
    [void][System.Diagnostics.Process]::Start($start)
} catch {
    if ($PrintArguments) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Crackdown performance test')
    exit 1
}
