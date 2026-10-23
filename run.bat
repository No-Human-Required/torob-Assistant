@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Torob In-Store Assistant - Local Server
echo ============================================
echo   Torob In-Store Assistant
echo   Starting local server on port 8787 ...
echo ============================================
echo.
start "" http://127.0.0.1:8787
python server.py
echo.
echo Server stopped. Press any key to close...
pause >nul
