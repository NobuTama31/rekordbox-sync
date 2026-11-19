# Rekordbox UI Automation capture: walks the rekordbox window's UIA
# tree and emits it as JSON rows (name/type/rect/offscreen, Edit values).
# This script only transports facts — every interpretation lives in the
# Python deck parser. Three modes:
#   (default)   write the snapshot to a file under logs/ (manual probe)
#   -StdoutJson print one snapshot as JSON (the one-shot source)
#   -Serve      answer one JSON request line with one response line,
#               re-reading only tracked elements between full walks
#               (the resident source; see docs/resident_uia.md)
param([string]$Label = 'manual', [switch]$StdoutJson, [string]$OutputDirectory = (Join-Path (Get-Location) 'logs'), [switch]$KeepHistory, [switch]$Serve)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
if (-not ('RekordboxProbeWindow' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RekordboxProbeWindow {
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsIconic(IntPtr hWnd);
}
'@
}
function Get-UiaRow {
    param($Element, [int]$Index)
    # Keep the per-element work minimal: every pattern or walker call is a
    # round trip serviced by Rekordbox's own UI thread, and a capture makes
    # thousands of them. The parser reads only name/type/rect/offscreen,
    # the Value of Edit controls (the BPM readout; every other type exposes
    # the same string through Name), and toggle states for diagnostics.
    $current = $Element.Current
    $rect = $current.BoundingRectangle
    $row = [ordered]@{
        index = $Index
        name = $current.Name
        type = $current.ControlType.ProgrammaticName
        automation_id = $current.AutomationId
        offscreen = $current.IsOffscreen
        rect = @{ x = $rect.X; y = $rect.Y; width = $rect.Width; height = $rect.Height }
    }
    if ($row['type'] -eq 'ControlType.Edit') {
        $pattern = $null
        if ($Element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            $row['value'] = $pattern.Current.Value
        }
    }
    $pattern = $null
    if ($Element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {
        $row['toggle_state'] = $pattern.Current.ToggleState.ToString()
    }
    [pscustomobject]$row
}

function Get-UiaRows {
    param($Root, $References = $null)
    $elements = $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    for ($index = 0; $index -lt $elements.Count; $index++) {
        $element = $elements.Item($index)
        if ($null -ne $References) { $References.Add($element) }
        Get-UiaRow -Element $element -Index $index
    }
}

function Set-UiaTrackedState {
    # After a full walk, remember the elements worth re-reading on the
    # next poll: the two BEAT SYNC anchors, the two play buttons, and
    # every visible Text/Edit in the deck band between them. The rule
    # is purely positional — a superset of what the parser reads — so
    # this script never decides what an element means.
    param($Rows, $References, $Root, $Capture)
    $script:trackedState = $null
    $visible = @($Rows | Where-Object { -not $_.offscreen })
    $anchors = @($visible | Where-Object { ($_.name -replace "`n", ' ') -ceq 'BEAT SYNC' })
    # Use code points so Windows PowerShell can also read this resource without a BOM.
    $japanesePlay = -join ([char[]]@(0x30D7, 0x30EC, 0x30A4, 0x2F, 0x30DD, 0x30FC, 0x30BA))
    $plays = @($visible | Where-Object { $_.name -ceq 'Play/Pause' -or $_.name -ceq $japanesePlay })
    if ($anchors.Count -ne 2 -or $plays.Count -ne 2) { return }
    $top = ($anchors | ForEach-Object { $_.rect.y } | Measure-Object -Minimum).Minimum - 16
    $bottom = ($plays | ForEach-Object { $_.rect.y + $_.rect.height } | Measure-Object -Maximum).Maximum + 40
    $anchorIndices = @($anchors | ForEach-Object { $_.index })
    $playIndices = @($plays | ForEach-Object { $_.index })
    $tracked = @($visible | Where-Object {
        $_.index -in $anchorIndices -or $_.index -in $playIndices -or
        ($_.type -cin @('ControlType.Text', 'ControlType.Edit') -and
         $_.rect.y -ge $top -and $_.rect.y -le $bottom)
    } | ForEach-Object {
        [pscustomobject]@{ element = $References[$_.index]; row = $_ }
    })
    $script:trackedState = @{
        entries = $tracked
        root = $Root
        process_id = $Capture.process_id
        executable = $Capture.executable
        full_walk_watch = [System.Diagnostics.Stopwatch]::StartNew()
    }
}

function Test-UiaTextChange {
    param([string]$Before, [string]$After)
    if ($Before -ceq $After) { return $false }
    # Only text shape is classified here, never the role of an element.
    # Both sides must be volatile: entering/leaving a timer/BPM shape is
    # uncertainty too (for example a blank readout while loading a track).
    $volatile = '\A(?:-?\d+:\d{2}(?:\.\d)?|\.\d|\s*\d+\.\d{2}\s*)\z'
    return -not ($Before -cmatch $volatile -and $After -cmatch $volatile)
}

function Get-ResidentUiaCapture {
    param([string]$CaptureLabel)
    if ($null -ne $script:trackedState -and $script:trackedState.full_walk_watch.Elapsed.TotalSeconds -lt 60) {
        try {
            $captureStartedAt = [DateTime]::UtcNow.ToString('o')
            $rows = @(
                foreach ($entry in $script:trackedState.entries) {
                    $row = Get-UiaRow -Element $entry.element -Index $entry.row.index
                    if ($row.offscreen -ne $entry.row.offscreen -or
                        (Test-UiaTextChange -Before $entry.row.name -After $row.name) -or
                        (Test-UiaTextChange -Before $entry.row.value -After $row.value)) {
                        throw 'Tracked content changed'
                    }
                    $row
                }
            )
            $current = $script:trackedState.root.Current
            $result = [ordered]@{
                received_at = [DateTime]::UtcNow.ToString('o')
                label = $CaptureLabel
                process_id = $script:trackedState.process_id
                executable = $script:trackedState.executable
                capture_started_at = $captureStartedAt
                window_name = $current.Name
                window_minimized = [RekordboxProbeWindow]::IsIconic([IntPtr]$current.NativeWindowHandle)
                elements = $rows
                capture_mode = 'tracked'
            }
            for ($index = 0; $index -lt $rows.Count; $index++) {
                $script:trackedState.entries[$index].row = $rows[$index]
            }
            return $result
        } catch {
            # An optimization miss is silent; the full walk is authoritative.
        }
    }
    $script:trackedState = $null
    $result = Get-UiaCapture -CaptureLabel $CaptureLabel
    $result['capture_mode'] = 'full'
    return $result
}

function Get-UiaRoot {
    param($ProcessId)
    # The process owns several top-level windows (the main window, a
    # tooltip, helpers). Picking the first one can root the walk at an
    # auxiliary window whose tree is empty — the "0 elements" capture
    # (observed live: window_name "tooltip"). The main window is the
    # largest one.
    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $ProcessId)
    $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)
    $best = $null
    $bestArea = -1.0
    for ($index = 0; $index -lt $windows.Count; $index++) {
        $window = $windows.Item($index)
        $rect = $window.Current.BoundingRectangle
        $area = [double]$rect.Width * [double]$rect.Height
        if ([double]::IsInfinity($area) -or [double]::IsNaN($area)) { $area = -1.0 }
        if ($area -gt $bestArea) { $bestArea = $area; $best = $window }
    }
    return $best
}

function Get-UiaCapture {
    param([string]$CaptureLabel)
    $captureStartedAt = [DateTime]::UtcNow.ToString('o')
    $rbProcess = Get-Process rekordbox | Select-Object -First 1
    $root = Get-UiaRoot -ProcessId $rbProcess.Id
    if ($null -eq $root) { throw 'Rekordbox window not found' }
    $references = if ($Serve.IsPresent) { ,(New-Object 'System.Collections.Generic.List[object]') } else { $null }
    $rows = @(Get-UiaRows -Root $root -References $references)
    $hasAnchor = @($rows | Where-Object { $_.type -eq 'ControlType.Button' -and ($_.name -replace "`n", ' ') -eq 'BEAT SYNC' }).Count -gt 0
    if (-not $hasAnchor) {
        # The walk is not atomic: under playback load it can return a
        # tree with whole subtrees missing (the decks are on screen yet
        # no BEAT SYNC anchor is in the tree). One immediate re-walk
        # usually sees the full tree. The root is re-selected too, in
        # case the first pick was an auxiliary window.
        $root = Get-UiaRoot -ProcessId $rbProcess.Id
        if ($null -eq $root) { throw 'Rekordbox window not found' }
        if ($null -ne $references) { $references.Clear() }
        $rows = @(Get-UiaRows -Root $root -References $references)
    }
    $result = [ordered]@{
        received_at = [DateTime]::UtcNow.ToString('o')
        label = $CaptureLabel
        process_id = $rbProcess.Id
        executable = $rbProcess.Path
        capture_started_at = $captureStartedAt
        window_name = $root.Current.Name
        window_minimized = [RekordboxProbeWindow]::IsIconic([IntPtr]$root.Current.NativeWindowHandle)
        elements = @($rows)
    }
    if ($Serve.IsPresent) { Set-UiaTrackedState -Rows $rows -References $references -Root $root -Capture $result }
    return $result
}

if ($Serve) {
    $script:trackedState = $null
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    while ($null -ne ($requestLine = [Console]::ReadLine())) {
        $requestLabel = $Label
        $requestStartedAt = [DateTime]::UtcNow.ToString('o')
        try {
            $request = $requestLine | ConvertFrom-Json
            if ($null -ne $request.label) { $requestLabel = [string]$request.label }
            $result = Get-ResidentUiaCapture -CaptureLabel $requestLabel
        } catch {
            $script:trackedState = $null
            $message = $_.Exception.Message
            [Console]::Error.WriteLine($message)
            $result = [ordered]@{
                received_at = [DateTime]::UtcNow.ToString('o')
                label = $requestLabel
                capture_started_at = $requestStartedAt
                elements = @()
                error = $message
                capture_mode = 'full'
            }
        }
        [Console]::WriteLine(($result | ConvertTo-Json -Depth 8 -Compress))
        [Console]::Out.Flush()
    }
    return
}

$result = Get-UiaCapture -CaptureLabel $Label
if ($StdoutJson) {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $result | ConvertTo-Json -Depth 8 -Compress
    return
}
$directory = $OutputDirectory
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$filename = if ($KeepHistory) { (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [Guid]::NewGuid().ToString('N') + '_uia.json' } else { 'latest_uia.json' }
$path = Join-Path $directory $filename
$temporaryPath = Join-Path $directory ([Guid]::NewGuid().ToString('N') + '.tmp')
try {
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $path -Force
} finally {
    if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath }
}
Write-Output "Saved $($result.elements.Count) elements: $path"
