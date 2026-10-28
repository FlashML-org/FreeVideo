@echo off
setlocal EnableDelayedExpansion

echo ===================================================
echo [FreeVideo] Run Example Videos Overnight Generator
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

echo [1/2] Verifying FreeVideo server connection on port 8189...
%PYTHON_CMD% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8189/system_stats', timeout=4)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [WARNING] FreeVideo / ComfyUI server is not responding on 127.0.0.1:8189.
    echo Please ensure the FreeVideo application or ComfyUI service is launched.
    echo ===================================================
    pause
    exit /b 1
)
echo [OK] Connected to FreeVideo service on 127.0.0.1:8189.

echo.
echo [2/2] Starting overnight batch generation process...
echo The process will run all categories sequentially:
echo   - 01 Pets: Playful tabby kitten in sunbeam
echo   - 02 Humans: Cyberpunk detective in rainy neon alley
echo   - 03 Mythical Creatures: Phoenix rebirth from embers
echo   - 04 Giant Creatures: Primordial stone colossus
echo   - 05 Aliens: Bioluminescent xenofauna in crystal cave
echo   - 06 Zombies: Foggy urban apocalypse encounter
echo   - 07 Multi-Scene Story: Harry Potter and Female Malfoy confession
echo.
echo Output destination: \examples_generated\
echo You can leave this running overnight (AFK).
echo ===================================================
echo.

%PYTHON_CMD% scripts\generate_examples.py
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
echo [SUCCESS] Overnight generation batch completed!
echo Check your videos and reference files in: \examples_generated\
echo ===================================================
pause
