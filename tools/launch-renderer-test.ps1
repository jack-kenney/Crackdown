param(
    [ValidateSet(60, 120, 144, 240)][int]$FrameRate = 240,
    [ValidateRange(0, 100)][double]$VehicleLod1Distance = 15,
    [switch]$Baseline,
    [ValidateSet('baseline', 'batched', 'ranges', 'constants', 'optimized', 'gpu', 'optimized-gpu')]
    [string]$Variant = 'batched',
    [string]$GpuTimingPath,
    [ValidateRange(1, 10000)][int]$GpuTimingInterval = 60,
    [switch]$GpuTimingDetails,
    [switch]$PrintArguments
)
if ($Baseline) {
    if ($Variant -ne 'batched' -and $Variant -ne 'baseline') { throw '-Baseline cannot be combined with another -Variant.' }
    $Variant = 'baseline'
}
& (Join-Path $PSScriptRoot 'launch-performance-test.ps1') -Renderer $Variant `
    -FrameRate $FrameRate -VehicleLod1Distance $VehicleLod1Distance `
    -GpuTimingPath $GpuTimingPath -GpuTimingInterval $GpuTimingInterval `
    -GpuTimingDetails:$GpuTimingDetails -PrintArguments:$PrintArguments
