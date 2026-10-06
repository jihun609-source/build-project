@echo off
rem Stop the AutoSet server (whatever process listens on the configured port)
cd /d "%~dp0"
set PYTHONUTF8=1
set PORT=8000
if exist ".venv\Scripts\python.exe" (
    for /f %%p in ('".venv\Scripts\python.exe" -m src.show_port 2^>nul') do set PORT=%%p
)
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $c) { Write-Output 'AutoSet server is not running (port %PORT%).'; exit 0 }; foreach ($x in $c) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $x.OwningProcess); if ($p.CommandLine -match 'src\.main') { Stop-Process -Id $x.OwningProcess -Force; Write-Output ('Stopped AutoSet server (port %PORT%, pid ' + $x.OwningProcess + ').') } else { Write-Output ('Port %PORT% is used by another program, not stopped: ' + $p.CommandLine) } }"
timeout /t 3 >nul 2>nul
