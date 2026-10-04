function Get-LauncherDefaults {
    return @{
        schemaVersion = 1; executablePath = ''; resolutionScale = 2
        antialiasing = 'none'; anisotropicOverride = 3
        fullscreen = $false; bloom = $true; shadows = $true
        fixLighting = $false; fixLightOcclusion = $true
        showFps = $false; showPerfgraph = $false; skipIntroMovies = $true
        audioQueueFrames = 8; directXinput = $false
    }
}

function Read-LauncherSettings([string]$Path) {
    $settings = Get-LauncherDefaults
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $settings }
    $saved = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($saved.schemaVersion -ne 1) { throw 'Unsupported launcher settings version.' }
    foreach ($key in @($settings.Keys)) {
        $property = $saved.PSObject.Properties[$key]
        if ($null -eq $property) { continue }
        $value = $property.Value
        if ($settings[$key] -is [bool]) {
            if ($value -isnot [bool]) { throw "Invalid setting: $key" }
        } elseif ($key -eq 'resolutionScale') {
            if (($value -isnot [int] -and $value -isnot [long]) -or $value -notin @(1, 2, 3)) { throw 'Invalid resolution.' }
            $value = [int]$value
        } elseif ($key -eq 'audioQueueFrames') {
            if (($value -isnot [int] -and $value -isnot [long]) -or $value -notin @(8, 16, 64)) { throw 'Invalid audio buffer size.' }
            $value = [int]$value
        } elseif ($key -eq 'anisotropicOverride') {
            if (($value -isnot [int] -and $value -isnot [long]) -or $value -notin @(-1, 0, 1, 2, 3, 4, 5)) { throw 'Invalid anisotropic filtering mode.' }
            $value = [int]$value
        } elseif ($key -eq 'antialiasing') {
            if ($value -isnot [string] -or $value -cnotin @('none', 'fxaa', 'fxaa_extreme')) { throw 'Invalid anti-aliasing mode.' }
        } elseif ($key -eq 'executablePath') {
            if ($value -isnot [string]) { throw 'Invalid executable path.' }
        }
        $settings[$key] = $value
    }
    return $settings
}

function Save-LauncherSettings([string]$Path, [hashtable]$Settings) {
    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($Path))
    [System.IO.File]::WriteAllText($Path, ($Settings | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
}

function Get-GameArguments([string]$Root, [hashtable]$Settings) {
    $flags = [ordered]@{
        game_data_root = (Join-Path $Root 'assets')
        user_data_root = (Join-Path $Root 'out/userdata')
        log_file = (Join-Path $Root 'out/launcher-game.log')
        enable_console = $false
        draw_resolution_scale_x = $Settings.resolutionScale
        draw_resolution_scale_y = $Settings.resolutionScale
        swap_post_effect = $Settings.antialiasing
        anisotropic_override = $Settings.anisotropicOverride
        fullscreen = $Settings.fullscreen
        fix_lighting = $Settings.fixLighting
        fix_light_occlusion = $Settings.fixLightOcclusion
        disable_bloom = (-not $Settings.bloom)
        disable_shadows = (-not $Settings.shadows)
        misc_performance_improvements = $false
        show_fps = $Settings.showFps
        show_perfgraph = $Settings.showPerfgraph
        skip_intro_movies = $Settings.skipIntroMovies
        audio_maxqframes = $Settings.audioQueueFrames
        sdl_direct_xinput = $Settings.directXinput
        high_resolution_timer = $true
        vsync = $true
    }
    foreach ($key in $flags.Keys) {
        $value = $flags[$key]
        if ($value -is [bool]) { $value = $value.ToString().ToLowerInvariant() }
        "--$key=$value"
    }
}

function ConvertTo-WindowsArgument([string]$Value) {
    # CommandLineToArgvW: double backslashes before quotes and the closing quote.
    return '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}
