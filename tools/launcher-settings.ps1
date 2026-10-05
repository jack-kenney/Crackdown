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

function Get-OptimizedBuild([string]$ExecutablePath) {
    if (-not $ExecutablePath) { return $null }
    $descriptorPath = Join-Path ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ExecutablePath))) 'optimized-build.json'
    if (-not (Test-Path -LiteralPath $descriptorPath -PathType Leaf)) { return $null }
    $descriptor = Get-Content -LiteralPath $descriptorPath -Raw | ConvertFrom-Json
    if ($descriptor.executableName -ne [IO.Path]::GetFileName($ExecutablePath)) { return $null }
    if ($descriptor.schemaVersion -ne 1 -or $descriptor.rendererVariant -ne 'frontend') {
        throw 'Unsupported optimized build descriptor. Rebuild with build-local.ps1 -Optimized.'
    }
    $directory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ExecutablePath))
    $expected = @{
        $descriptor.executableName = $descriptor.executableSha256
        'rexruntime.dll' = $descriptor.dllSha256.'rexruntime.dll'
        'rexgpu-xenos.dll' = $descriptor.dllSha256.'rexgpu-xenos.dll'
    }
    foreach ($file in $expected.Keys) {
        $path = Join-Path $directory $file
        if ($expected[$file] -notmatch '^[0-9a-fA-F]{64}$' -or
            -not (Test-Path -LiteralPath $path -PathType Leaf) -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected[$file]) {
            throw 'Optimized executable and renderer do not match. Rebuild with build-local.ps1 -Optimized.'
        }
    }
    return $descriptor
}

function Assert-LauncherProfileAvailable([string]$Profile) {
    if (Get-CimInstance Win32_Process -Filter "Name = 'crackdown.exe' OR Name = 'crackdown-performance.exe' OR Name = 'crackdown-renderer.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($Profile, [StringComparison]::OrdinalIgnoreCase) -ge 0 }) {
        throw 'Close the existing game using this save profile before launching another.'
    }
}

function Get-GameArguments([string]$Root, [hashtable]$Settings, [switch]$IgnoreBuildProfile) {
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
    $executablePath = $Settings.executablePath
    if (-not $executablePath) { $executablePath = Join-Path $Root 'out/build/win-amd64-release/crackdown.exe' }
    if (-not $IgnoreBuildProfile -and (Get-OptimizedBuild $executablePath)) {
        $flags.user_data_root = Join-Path $Root 'out/userdata-renderer-test'
        $flags.log_file = Join-Path $Root 'out/optimized-game.log'
        $flags.native_frame_rate = 240
        $flags.native_discard_hitch_time = $true
        $flags.fix_guest_event_clear = $true
        $flags.pace_gpu_wait = $true
        $flags.vehicle_lod1_distance = 15
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
