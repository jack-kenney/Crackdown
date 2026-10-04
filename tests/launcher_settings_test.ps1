$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../tools/launcher-settings.ps1')

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('crackdown-launcher-test-' + [Guid]::NewGuid())
$settingsPath = Join-Path $temporary 'settings.json'
[void][IO.Directory]::CreateDirectory($temporary)
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
    Write-Host 'Launcher settings: compatibility, all filtering modes, SDK flags and invalid values passed.'
} finally {
    # Only these known temporary files/directories belong to this test.
    if (Test-Path -LiteralPath $settingsPath) { Remove-Item -LiteralPath $settingsPath }
    Remove-Item -LiteralPath $temporary
}
