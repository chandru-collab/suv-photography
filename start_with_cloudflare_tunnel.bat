@echo off
title SUV Photography - Cloudflare Public Tunnel Server
cd /d "%~dp0"

echo ======================================================================
echo   SUV Photography - Live Cloudflare Public Tunnel
echo ======================================================================
echo.

echo Starting Local Python Server on port 8000...
start "SUV Python Server" cmd /k ".\.venv\Scripts\python.exe run_production.py"

timeout /t 3 /nobreak >nul

echo Starting Cloudflare Secure Public HTTPS Tunnel...
echo.
.\cloudflared.exe tunnel --url http://127.0.0.1:8000

pause
