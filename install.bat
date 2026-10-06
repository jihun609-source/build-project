@echo off
rem First-time setup: Python venv + packages, .env, web UI build. Safe to run again.
cd /d "%~dp0"
set PYTHONUTF8=1

rem 1) Python 3.11+ venv
if exist ".venv\Scripts\python.exe" goto venv_ok
echo [1/4] Creating Python venv...
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv .venv
    goto venv_check
)
py -3.12 -m venv .venv 2>nul && goto venv_check
py -3.11 -m venv .venv 2>nul && goto venv_check
python -m venv .venv
:venv_check
if not exist ".venv\Scripts\python.exe" (
    echo Python 3.11 or newer is required. Install with: winget install Python.Python.3.12
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo .venv was created with Python older than 3.11. Delete .venv, install Python 3.12, then run again.
    pause
    exit /b 1
)
goto venv_done
:venv_ok
echo [1/4] .venv already exists
:venv_done

echo [2/4] Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
)

echo [3/4] Preparing .env
if not exist ".env" copy ".env.example" ".env" >nul

echo [4/4] Building web UI...
call build_web.bat
if errorlevel 1 (
    echo Web UI build failed. Node.js 20+ is required: winget install OpenJS.NodeJS.LTS
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul || echo [warn] ffmpeg not found in PATH. Install it or set paths.ffmpeg in config.yaml.
where ollama >nul 2>nul || echo [warn] Ollama not found. Install it and run: ollama pull gemma4:e4b

echo.
echo Setup complete. Start with run.bat
pause
