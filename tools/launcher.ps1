param(
    [string]$ExecutablePath = '',
    [string]$PreviewPath = '',
    [switch]$PrintArguments
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'launcher-settings.ps1')
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$settingsPath = Join-Path $root 'out/launcher-settings.json'
$settingsError = ''
try { $settings = Read-LauncherSettings $settingsPath }
catch { $settings = Get-LauncherDefaults; $settingsError = 'Saved settings could not be read. Defaults have been restored.' }
if ($ExecutablePath) { $settings.executablePath = [System.IO.Path]::GetFullPath($ExecutablePath) }
if (-not $settings.executablePath) { $settings.executablePath = Join-Path $root 'out/build/win-amd64-release/crackdown.exe' }
if ($PrintArguments) {
    [pscustomobject]@{ executable = $settings.executablePath; arguments = @(Get-GameArguments $root $settings) } | ConvertTo-Json
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = [System.Windows.Forms.Form]::new()
$form.Text = 'Crackdown Launcher'
$form.ClientSize = [System.Drawing.Size]::new(720, 625)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.AutoScaleMode = 'Dpi'
$form.Font = [System.Drawing.Font]::new('Segoe UI', 10)
$form.BackColor = [System.Drawing.Color]::FromArgb(246, 248, 252)

function Add-Label($Parent, [string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height = 26) {
    $control = [System.Windows.Forms.Label]::new()
    $control.Text = $Text
    $control.Location = [System.Drawing.Point]::new($X, $Y)
    $control.Size = [System.Drawing.Size]::new($Width, $Height)
    $Parent.Controls.Add($control)
    return $control
}
function Add-Check($Parent, [string]$Text, [int]$X, [int]$Y, [int]$Width = 600) {
    $control = [System.Windows.Forms.CheckBox]::new()
    $control.Text = $Text
    $control.Location = [System.Drawing.Point]::new($X, $Y)
    $control.Size = [System.Drawing.Size]::new($Width, 28)
    $Parent.Controls.Add($control)
    return $control
}
function Add-Combo($Parent, [string[]]$Items, [int]$X, [int]$Y, [int]$Width = 340) {
    $control = [System.Windows.Forms.ComboBox]::new()
    $control.DropDownStyle = 'DropDownList'
    $control.Location = [System.Drawing.Point]::new($X, $Y)
    $control.Size = [System.Drawing.Size]::new($Width, 30)
    $control.Items.AddRange($Items)
    $Parent.Controls.Add($control)
    return $control
}
function Add-Button([string]$Text, [int]$X, [int]$Width) {
    $control = [System.Windows.Forms.Button]::new()
    $control.Text = $Text
    $control.Location = [System.Drawing.Point]::new($X, 563)
    $control.Size = [System.Drawing.Size]::new($Width, 40)
    $form.Controls.Add($control)
    return $control
}

$title = Add-Label $form 'CRACKDOWN' 24 16 660 38
$title.Font = [System.Drawing.Font]::new('Segoe UI', 23, [System.Drawing.FontStyle]::Bold)
$title.ForeColor = [System.Drawing.Color]::FromArgb(25, 48, 87)
[void](Add-Label $form 'Choose your settings, then launch. Changes apply to the next game session.' 26 62 670)
$tabs = [System.Windows.Forms.TabControl]::new()
$tabs.Location = [System.Drawing.Point]::new(24, 102)
$tabs.Size = [System.Drawing.Size]::new(672, 348)
$form.Controls.Add($tabs)
$graphics = [System.Windows.Forms.TabPage]::new('Graphics')
$game = [System.Windows.Forms.TabPage]::new('Game & audio')
$graphics.BackColor = [System.Drawing.Color]::White
$game.BackColor = [System.Drawing.Color]::White
$tabs.TabPages.AddRange(@($graphics, $game))

[void](Add-Label $graphics 'Render resolution' 18 18 190)
$resolution = Add-Combo $graphics @('1280 x 720 (original)', '2560 x 1440', '3840 x 2160 (experimental)') 210 15 423
[void](Add-Label $graphics 'Anti-aliasing' 18 62 190)
$antialiasing = Add-Combo $graphics @('Original game rendering', 'FXAA', 'FXAA - higher quality') 210 59 423
$fullscreen = Add-Check $graphics 'Borderless fullscreen' 18 104
$bloom = Add-Check $graphics 'Bloom' 18 143 190
$shadows = Add-Check $graphics 'Shadows' 210 143 220
$lighting = Add-Check $graphics 'Lighting accuracy fix (costly)' 18 182
$occlusion = Add-Check $graphics 'Hide light halos that show through walls' 18 221
$graphicsNote = Add-Label $graphics '1440p is tested. 4K is experimental. FXAA smooths the final image; original textures remain.' 18 264 615 48
$graphicsNote.ForeColor = [System.Drawing.Color]::FromArgb(80, 92, 112)
$lighting.Add_CheckedChanged({
    if ($lighting.Checked) {
        $graphicsNote.Text = 'The lighting fix reads render results back to the CPU and can substantially reduce frame rate. It is off by default.'
        $graphicsNote.ForeColor = [System.Drawing.Color]::FromArgb(155, 90, 0)
    } else {
        $graphicsNote.Text = '1440p is tested. 4K is experimental. FXAA smooths the final image; original textures remain.'
        $graphicsNote.ForeColor = [System.Drawing.Color]::FromArgb(80, 92, 112)
    }
})

$fps = Add-Check $game 'FPS counter' 18 15
$perfgraph = Add-Check $game 'FPS counter + performance graph' 18 53
$intro = Add-Check $game 'Skip Microsoft and Realtime Worlds intro movies' 18 91
[void](Add-Label $game 'Audio buffering' 18 146 190)
$audio = Add-Combo $game @('Low latency (8 blocks, ~43 ms)', 'More buffering (16 blocks, ~85 ms)', 'Original buffering (64 blocks, ~341 ms)') 210 143 423
[void](Add-Label $game 'Controller polling' 18 192 190)
$controllerPolling = Add-Combo $game @('Standard SDL polling', 'Direct XInput (Xbox controllers)') 210 189 423
[void](Add-Label $game 'VSync and timer precision retain the tested timing settings. Campaign cutscenes remain available.' 18 248 615 48)

[void](Add-Label $form 'Game executable' 24 465 150)
$executable = [System.Windows.Forms.TextBox]::new()
$executable.Location = [System.Drawing.Point]::new(174, 462)
$executable.Size = [System.Drawing.Size]::new(422, 28)
$form.Controls.Add($executable)
$browse = [System.Windows.Forms.Button]::new()
$browse.Text = 'Browse...'
$browse.Location = [System.Drawing.Point]::new(604, 460)
$browse.Size = [System.Drawing.Size]::new(92, 30)
$form.Controls.Add($browse)
$status = Add-Label $form 'Settings are saved locally. F4 opens additional settings in the game.' 24 508 672 42
$status.ForeColor = [System.Drawing.Color]::FromArgb(80, 92, 112)
$reset = Add-Button 'Reset defaults' 24 152
$save = Add-Button 'Save settings' 384 145
$launch = Add-Button 'Launch game' 543 153
$launch.BackColor = [System.Drawing.Color]::FromArgb(34, 88, 177)
$launch.ForeColor = [System.Drawing.Color]::White
$launch.FlatStyle = 'Flat'
$form.AcceptButton = $launch

function Set-Controls([hashtable]$Values) {
    $resolution.SelectedIndex = $Values.resolutionScale - 1
    $antialiasing.SelectedIndex = @('none', 'fxaa', 'fxaa_extreme').IndexOf($Values.antialiasing)
    $fullscreen.Checked = $Values.fullscreen
    $bloom.Checked = $Values.bloom
    $shadows.Checked = $Values.shadows
    $lighting.Checked = $Values.fixLighting
    $occlusion.Checked = $Values.fixLightOcclusion
    $fps.Checked = $Values.showFps
    $perfgraph.Checked = $Values.showPerfgraph
    $intro.Checked = $Values.skipIntroMovies
    $audio.SelectedIndex = @(8, 16, 64).IndexOf($Values.audioQueueFrames)
    $controllerPolling.SelectedIndex = [int]$Values.directXinput
    $executable.Text = $Values.executablePath
}
function Get-Controls {
    return @{
        schemaVersion = 1; executablePath = $executable.Text.Trim()
        resolutionScale = $resolution.SelectedIndex + 1
        antialiasing = @('none', 'fxaa', 'fxaa_extreme')[$antialiasing.SelectedIndex]
        fullscreen = $fullscreen.Checked; bloom = $bloom.Checked; shadows = $shadows.Checked
        fixLighting = $lighting.Checked; fixLightOcclusion = $occlusion.Checked
        showFps = $fps.Checked; showPerfgraph = $perfgraph.Checked
        skipIntroMovies = $intro.Checked; audioQueueFrames = @(8, 16, 64)[$audio.SelectedIndex]
        directXinput = ($controllerPolling.SelectedIndex -eq 1)
    }
}
function Show-Error([string]$Message) {
    [void][System.Windows.Forms.MessageBox]::Show($form, $Message, 'Crackdown Launcher', 'OK', 'Error')
}
$browse.Add_Click({
    $dialog = [System.Windows.Forms.OpenFileDialog]::new()
    $dialog.Title = 'Choose the Crackdown executable'
    $dialog.Filter = 'Crackdown executable (crackdown.exe)|crackdown.exe'
    try { if ($dialog.ShowDialog($form) -eq 'OK') { $executable.Text = $dialog.FileName } }
    finally { $dialog.Dispose() }
})
$reset.Add_Click({
    $values = Get-LauncherDefaults
    $values.executablePath = $executable.Text
    Set-Controls $values
    $status.Text = 'Defaults restored. Save or launch to keep them.'
})
$save.Add_Click({
    try { Save-LauncherSettings $settingsPath (Get-Controls); $status.Text = 'Settings saved. They apply on your next launch.' }
    catch { Show-Error $_.Exception.Message }
})
$launch.Add_Click({
    try {
        $values = Get-Controls
        if (-not (Test-Path -LiteralPath $values.executablePath -PathType Leaf)) {
            throw 'The game executable was not found. Build with build-local.ps1, or choose your existing crackdown.exe.'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $root 'assets/default.xex') -PathType Leaf)) {
            throw 'Game files were not found. Extract your TU0 disc into assets, with default.xex directly inside it.'
        }
        Save-LauncherSettings $settingsPath $values
        $start = [System.Diagnostics.ProcessStartInfo]::new()
        $start.FileName = [System.IO.Path]::GetFullPath($values.executablePath)
        $start.WorkingDirectory = $root
        $start.UseShellExecute = $false
        $start.Arguments = (@(Get-GameArguments $root $values) | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
        $process = [System.Diagnostics.Process]::Start($start)
        $status.Text = "Game launched (PID $($process.Id)). Settings changes apply to the next launch."
        $process.Dispose()
        $form.WindowState = 'Minimized'
    } catch { Show-Error $_.Exception.Message }
})
Set-Controls $settings
if ($settingsError) { $status.Text = $settingsError }
if ($PreviewPath) {
    $form.Show()
    [System.Windows.Forms.Application]::DoEvents()
    $bitmap = [System.Drawing.Bitmap]::new($form.Width, $form.Height)
    try {
        $form.DrawToBitmap($bitmap, [System.Drawing.Rectangle]::new(0, 0, $form.Width, $form.Height))
        $bitmap.Save([System.IO.Path]::GetFullPath($PreviewPath), [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $bitmap.Dispose(); $form.Dispose() }
} else {
    try { [void]$form.ShowDialog() } finally { $form.Dispose() }
}
