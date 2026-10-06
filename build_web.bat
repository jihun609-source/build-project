@echo off
rem Build web UI into web\dist, then restart the server
cd /d "%~dp0web"
set "PATH=C:\Program Files\nodejs;%PATH%"
if not exist node_modules call npm install
call npm run build
