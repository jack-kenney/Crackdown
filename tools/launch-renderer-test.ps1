param(
    [ValidateSet(60, 120, 144, 240)][int]$FrameRate = 240,
    [ValidateRange(0, 100)][double]$VehicleLod1Distance = 15,
    [switch]$Baseline,
    [switch]$PrintArguments
)
$renderer = if ($Baseline) { 'baseline' } else { 'batched' }
& (Join-Path $PSScriptRoot 'launch-performance-test.ps1') -Renderer $renderer `
    -FrameRate $FrameRate -VehicleLod1Distance $VehicleLod1Distance -PrintArguments:$PrintArguments
