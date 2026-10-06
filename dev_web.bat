@echo off
rem Dev: Vite on 5173, proxies API to AUTOSET_PORT (default 8000)
cd /d "%~dp0web"
set "PATH=C:\Program Files\nodejs;%PATH%"
if "%AUTOSET_PORT%"=="" set AUTOSET_PORT=8000
if not exist node_modules call npm install
call npm run dev
