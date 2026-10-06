@echo off
rem Start AutoSet server + workers, then open the web UI when the server is ready
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo [AutoSet] .venv not found. Run install.bat first.
    pause
    exit /b 1
)

rem Port from config.yaml (default 8000 before first run)
set PORT=8000
for /f %%p in ('".venv\Scripts\python.exe" -m src.show_port 2^>nul') do set PORT=%%p

rem Wait in a separate hidden window until /health answers, then open the browser once.
rem (Do not use start /b here: -WindowStyle Hidden would hide this console too.)
title AutoSet server - close this window or press Ctrl+C to stop
start "AutoSet browser opener" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 90;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:%PORT%/health' | Out-Null; Start-Process 'http://localhost:%PORT%/ui/'; break}catch{Start-Sleep -Seconds 1}}"

".venv\Scripts\python.exe" -m src.main
if errorlevel 1 (
    echo AutoSet exited with an error. Check logs\api.log.
    pause
)
