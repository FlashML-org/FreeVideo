@echo off
setlocal EnableDelayedExpansion

echo ===================================================
echo [FreeVideo] Multi-Tier (5s, 10s, 15s) Overnight Batch
echo ===================================================

set "PYTHON_CMD=python"
python -c "import sys; exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if exist "C:\Python312\python.exe" (
        set "PYTHON_CMD=C:\Python312\python.exe"
    ) else (
        echo [ERROR] Python 3.12 is required but not found in PATH or at C:\Python312\python.exe.
        pause
        exit /b 1
    )
)

echo [1/2] Checking ComfyUI server status on port 8189...
%PYTHON_CMD% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8189/system_stats', timeout=5)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [WARNING] FreeVideo server is not reachable on 127.0.0.1:8189.
    echo Please ensure the FreeVideo service is running.
    echo ===================================================
    pause
    exit /b 1
)
echo [OK] Connected to FreeVideo service on 127.0.0.1:8189.

echo.
echo [2/2] Launching autonomous multi-tier batch generator...
echo This will run:
echo   - 5 Pop-Culture Scenarios (Goku/Bean, Neo/Wick, Godzilla/Dragonzord, Trek/Alien, Droids/Zombies)
echo   - Duration Tiers: 5s, 10s, and 15s (via Continuation Flow)
echo   - 5-Clip Cinematic Montage: Harry Potter & Female Malfoy (5 clips x 5s) stitched with D-Minor score
echo.
echo Outputs stored in: \examples_generated\
echo Logs recorded in:   \_project\artifacts\generation_runs_report_v1.0.md
echo You can leave this running completely AFK.
echo ===================================================
echo.

%PYTHON_CMD% scripts\generate_multitier_examples.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ===================================================
    echo [CRITICAL ERROR] The application crashed!
    echo Please read the traceback above to identify the issue.
    echo ===================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo [SUCCESS] All multi-tier runs completed successfully!
echo ===================================================
pause
