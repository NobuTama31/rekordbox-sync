param(
    [ValidateRange(1, 3600)][int]$Seconds = 120,
    [ValidateRange(1, 60)][int]$IntervalSeconds = 2,
    [string]$Label = 'playback-tempo-test',
    [switch]$KeepHistory
)
$ErrorActionPreference = 'Stop'
$timer = [Diagnostics.Stopwatch]::StartNew()
$count = 0
while ($timer.Elapsed.TotalSeconds -lt $Seconds) {
    & (Join-Path $PSScriptRoot 'capture_uia.ps1') -Label $Label -KeepHistory:$KeepHistory
    $count++
    Start-Sleep -Seconds $IntervalSeconds
}
Write-Output "Capture finished: $count snapshots."
