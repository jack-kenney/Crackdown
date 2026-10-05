$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../tools/launcher-settings.ps1')

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('crackdown-launcher-test-' + [Guid]::NewGuid())
$settingsPath = Join-Path $temporary 'settings.json'
[void][IO.Directory]::CreateDirectory($temporary)
$fixtureFiles = @('crackdown.exe', 'rexruntime.dll', 'rexgpu-xenos.dll', 'optimized-build.json')
try {
    $settings = Read-LauncherSettings $settingsPath
    Assert ($settings.anisotropicOverride -eq 3) 'Missing preferences must retain SDK 4x default.'

    # Pre-enhancement preferences must preserve choices and gain the old SDK default.
    [IO.File]::WriteAllText($settingsPath, '{"schemaVersion":1,"resolutionScale":3,"antialiasing":"fxaa","fullscreen":true}')
    $settings = Read-LauncherSettings $settingsPath
    Assert ($settings.anisotropicOverride -eq 3 -and $settings.resolutionScale -eq 3 -and $settings.antialiasing -eq 'fxaa' -and $settings.fullscreen) 'Old preferences did not migrate without losing choices.'

    foreach ($mode in @(-1, 0, 1, 2, 3, 4, 5)) {
        $settings.anisotropicOverride = $mode
        Save-LauncherSettings $settingsPath $settings
        $loaded = Read-LauncherSettings $settingsPath
        Assert ($loaded.anisotropicOverride -eq $mode) "Filtering mode $mode did not round-trip."
        $arguments = @(Get-GameArguments 'C:\Game Root' $loaded)
        Assert (($arguments | Where-Object { $_ -eq "--anisotropic_override=$mode" }).Count -eq 1) "Filtering mode $mode did not produce exactly one SDK flag."
        Assert ($arguments -contains '--vsync=true') 'Filtering changed guest pacing.'
    }
    foreach ($invalid in @('6', '-2', '3.5', '"5"', 'true', 'null')) {
        [IO.File]::WriteAllText($settingsPath, ('{"schemaVersion":1,"anisotropicOverride":' + $invalid + '}'))
        $rejected = $false
        try { $null = Read-LauncherSettings $settingsPath } catch { $rejected = $true }
        Assert $rejected "Invalid filtering value $invalid was accepted."
    }
    $settings = Get-LauncherDefaults
    $settings.executablePath = Join-Path $temporary 'crackdown.exe'
    $plain = @(Get-GameArguments $temporary $settings)
    Assert (-not ($plain | Where-Object { $_.StartsWith('--native_frame_rate=') })) 'An ordinary build gained optimized timing flags.'
    foreach ($file in @('crackdown.exe', 'rexruntime.dll', 'rexgpu-xenos.dll')) {
        [IO.File]::WriteAllText((Join-Path $temporary $file), "Test bytes for $file")
    }
    $descriptor = @{
        schemaVersion = 1; executableName = 'crackdown.exe'; rendererVariant = 'frontend'
        executableSha256 = (Get-FileHash $settings.executablePath -Algorithm SHA256).Hash
        dllSha256 = @{
            'rexruntime.dll' = (Get-FileHash (Join-Path $temporary 'rexruntime.dll') -Algorithm SHA256).Hash
            'rexgpu-xenos.dll' = (Get-FileHash (Join-Path $temporary 'rexgpu-xenos.dll') -Algorithm SHA256).Hash
        }
    }
    $descriptorPath = Join-Path $temporary 'optimized-build.json'
    [IO.File]::WriteAllText($descriptorPath, ($descriptor | ConvertTo-Json))
    $optimized = @(Get-GameArguments $temporary $settings)
    foreach ($flag in @('--native_frame_rate=240', '--native_discard_hitch_time=true', '--fix_guest_event_clear=true', '--pace_gpu_wait=true', '--vehicle_lod1_distance=15')) {
        Assert (($optimized | Where-Object { $_ -eq $flag }).Count -eq 1) "Optimized launch requires exactly one $flag."
    }
    Assert ($optimized -contains "--user_data_root=$(Join-Path $temporary 'out/userdata-renderer-test')") 'Optimized GUI launch must retain the tested save profile.'
    Assert ($optimized -contains '--draw_resolution_scale_x=2') 'Optimized defaults lost graphics preferences.'
    $neutral = @(Get-GameArguments $temporary $settings -IgnoreBuildProfile)
    Assert (-not ($neutral | Where-Object { $_.StartsWith('--native_frame_rate=') })) 'Explicit performance variants inherited GUI timing defaults.'
    Assert ($neutral -contains "--user_data_root=$(Join-Path $temporary 'out/userdata')") 'Explicit performance variants inherited GUI profile overrides.'
    foreach ($file in @('crackdown.exe', 'rexruntime.dll', 'rexgpu-xenos.dll')) {
        $path = Join-Path $temporary $file
        $original = [IO.File]::ReadAllText($path)
        [IO.File]::AppendAllText($path, 'stale build')
        $rejected = $false
        try { $null = @(Get-GameArguments $temporary $settings) } catch { $rejected = $true }
        Assert $rejected "Mismatched optimized $file was accepted."
        [IO.File]::WriteAllText($path, $original)
    }
    Remove-Item -LiteralPath (Join-Path $temporary 'rexgpu-xenos.dll')
    $rejected = $false
    try { $null = @(Get-GameArguments $temporary $settings) } catch { $rejected = $true }
    Assert $rejected 'An incomplete optimized package was accepted.'
    Write-Host 'Launcher settings: compatibility, filtering, optimized timing/profile, variant isolation and build hash checks passed.'
} finally {
    # Only these known temporary files/directories belong to this test.
    if (Test-Path -LiteralPath $settingsPath) { Remove-Item -LiteralPath $settingsPath }
    foreach ($file in $fixtureFiles) {
        $path = Join-Path $temporary $file
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
    Remove-Item -LiteralPath $temporary
}
