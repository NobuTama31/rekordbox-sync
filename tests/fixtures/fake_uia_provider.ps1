# Fake only the provider. Production enumeration, row reads, tracked selection,
# fallback, framing, and serialization all run against these objects.
$script:frames = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'frames.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$script:walks = 0
$script:fakeRoot = [pscustomobject]@{
    Current = [pscustomobject]@{ Name = 'fake'; NativeWindowHandle = 0 }
    elements = (New-Object 'System.Collections.Generic.List[object]')
}

function New-FakeElement {
    param($Row)
    $element = [pscustomobject]@{ row = $Row; stale = $false }
    $element | Add-Member -MemberType ScriptProperty -Name Current -Value {
        $row = $this.row
        [pscustomobject]@{
            Name = $row.name
            ControlType = [pscustomobject]@{ ProgrammaticName = $row.type }
            AutomationId = $row.automation_id
            IsOffscreen = $row.offscreen
            BoundingRectangle = [pscustomobject]@{
                X = $row.rect.x; Y = $row.rect.y
                Width = $row.rect.width; Height = $row.rect.height
            }
        }
    }
    $element | Add-Member -MemberType ScriptMethod -Name TryGetCurrentPattern -Value {
        param($Pattern, $Result)
        if ($this.stale) { throw 'stale fake element' }
        if ($Pattern -eq [System.Windows.Automation.ValuePattern]::Pattern -and
            $null -ne $this.row.PSObject.Properties['value']) {
            $Result.Value = [pscustomobject]@{ Current = [pscustomobject]@{ Value = $this.row.value } }
            return $true
        }
        if ($Pattern -eq [System.Windows.Automation.TogglePattern]::Pattern -and
            $null -ne $this.row.PSObject.Properties['toggle_state']) {
            $Result.Value = [pscustomobject]@{ Current = [pscustomobject]@{ ToggleState = $this.row.toggle_state } }
            return $true
        }
        return $false
    }
    return $element
}

$script:fakeRoot | Add-Member -MemberType ScriptMethod -Name FindAll -Value {
    param($Scope, $Condition)
    $script:walks++
    $this.Current.Name = "fake-$script:frameIndex/walks=$script:walks"
    if ($script:frame.fail_full) { throw 'full walk failed' }
    $this.elements = New-Object 'System.Collections.Generic.List[object]'
    if ($script:emptyWalks -gt 0) {
        $script:emptyWalks--
    } else {
        foreach ($row in $script:frame.elements) {
            $this.elements.Add((New-FakeElement -Row $row))
        }
    }
    return ,$this.elements
}

function Set-FakeFrame {
    param([int]$Index)
    $script:frameIndex = $Index
    $script:frame = $script:frames[$Index]
    $script:emptyWalks = $script:frame.empty_walks
    $script:fakeRoot.Current.Name = "fake-$Index/walks=$script:walks"
    foreach ($element in $script:fakeRoot.elements) {
        $index = $element.row.index
        if ($index -ge $script:frame.elements.Count) {
            $element.stale = $true
        } else {
            $element.row = $script:frame.elements[$index]
            $element.stale = $index -eq $script:frame.stale_index
        }
    }
    if ($null -ne $script:trackedState -and $null -ne $script:frame.elapsed_seconds) {
        $script:trackedState.full_walk_watch = [pscustomobject]@{
            Elapsed = [TimeSpan]::FromSeconds($script:frame.elapsed_seconds)
        }
    }
}

Set-FakeFrame -Index 0
