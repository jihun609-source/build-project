@echo off
setlocal
cd /d "%~dp0"
if not exist .venv-build\Scripts\python.exe py -3.12 -m venv .venv-build
if errorlevel 1 goto fail
.venv-build\Scripts\python.exe -m pip install -r packaging\requirements.txt
if errorlevel 1 goto fail
call npm --prefix web ci
if errorlevel 1 goto fail
call npm --prefix web run build
if errorlevel 1 goto fail
.venv-build\Scripts\python.exe packaging\build.py %*
if errorlevel 1 goto fail
echo Packages created in releases.
exit /b 0
:fail
echo Build failed. See packaging/README.md for prerequisites.
pause
exit /b 1
