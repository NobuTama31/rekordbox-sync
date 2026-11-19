$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$catalog = Join-Path $projectRoot 'assets/song_info.csv'
$arguments = @('-m', 'rekordbox_sync', 'gui', '--catalog', ('"' + $catalog + '"'), '--start-api')
Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -Wait
