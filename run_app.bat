@echo off
title SUV Photography - Event Photo Sharing Server
cd /d "%~dp0"
echo ======================================================================
echo   Starting SUV Photography Production Server
echo   Host Dashboard: http://localhost:8000/dashboard
echo   Drive Gallery : http://localhost:8000/gallery
echo   Guest Page    : http://localhost:8000/guest
echo   Host Password : photo123
echo ======================================================================
echo.
.\.venv\Scripts\python.exe run_production.py
pause
