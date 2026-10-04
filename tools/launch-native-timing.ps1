param(
    [ValidateSet(60, 120, 144, 240)][int]$FrameRate = 60,
    [switch]$PrintArguments
)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'launcher-settings.ps1')
    $root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $executable = Join-Path $root 'out/build/win-amd64-experiments/crackdown.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'Build the experimental executable first with build-local.ps1 -Experiments.'
    }
    $settings = Read-LauncherSettings (Join-Path $root 'out/launcher-settings.json')
    $profile = Join-Path $root 'out/userdata-fps-experimental'
    $arguments = @(Get-GameArguments $root $settings | Where-Object {
        -not $_.StartsWith('--user_data_root=') -and -not $_.StartsWith('--log_file=')
    })
    $arguments += "--user_data_root=$profile"
    $arguments += "--log_file=$(Join-Path $root 'out/native-timing-game.log')"
    $arguments += "--native_frame_rate=$FrameRate"
    if ($PrintArguments) {
        [pscustomobject]@{ executable = $executable; arguments = $arguments } | ConvertTo-Json
        exit 0
    }
    # Seed once from the regular profile. Later experimental sessions retain
    # their own progress; never copy experimental saves back automatically.
    if (-not (Test-Path -LiteralPath $profile)) {
        $sourceProfile = Join-Path $root 'out/userdata'
        if (Test-Path -LiteralPath $sourceProfile -PathType Container) {
            Copy-Item -LiteralPath $sourceProfile -Destination $profile -Recurse
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
    [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Crackdown experimental launcher')
    exit 1
}
