param([string]$Label = 'manual', [switch]$KeepHistory)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$captureArguments = @('-m', 'rekordbox_sync', 'capture-uia', '--label', $Label, '--output-dir', (Join-Path $projectRoot 'logs'))
if ($KeepHistory) { $captureArguments += '--keep-history' }
& $python @captureArguments
if ($LASTEXITCODE -ne 0) { throw 'UI capture failed' }
