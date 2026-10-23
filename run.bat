@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Torob In-Store Assistant - Local Server
echo ============================================
echo   Torob In-Store Assistant
echo   Local server: http://127.0.0.1:8787
echo ============================================
echo.

rem --- If a server is already answering on 8787, reuse it instead of starting a second one. ---
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8787/api/health'; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel%==0 (
    echo Server is already running on port 8787. Opening browser only.
    echo If search still misbehaves, close the old console window and run this file again.
    start "" http://127.0.0.1:8787
    "%SystemRoot%\System32\timeout.exe" /t 3 >nul 2>&1
    exit /b 0
)

rem --- Open the browser only after the server is actually listening. ---
start "" /b powershell -NoProfile -Command "for ($i=0; $i -lt 40; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://127.0.0.1:8787/api/health' | Out-Null; Start-Process 'http://127.0.0.1:8787'; break } catch { Start-Sleep -Milliseconds 500 } }"

python server.py
echo.
echo Server stopped. Press any key to close...
pause >nul
