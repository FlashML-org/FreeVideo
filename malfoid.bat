@echo off
setlocal EnableDelayedExpansion

echo ===================================================
echo [FreeVideo] Fem Malfoy: Battle of Hogwarts Story
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
    echo Please ensure the FreeVideo service is running before launching.
    echo ===================================================
    pause
    exit /b 1
)
echo [OK] Connected to FreeVideo service on 127.0.0.1:8189.

echo.
echo [2/2] Launching 'Fem Malfoy: Battle of Hogwarts' Story Pipeline...
echo This will render:
echo   - 10 narrative scenes (42s total film) with variable shot pacing (2.8s - 5.5s)
echo   - Master Story Bible enforcing cross-scene lighting, character anchors and fixed staging
echo   - Multi-shot camera cuts ([Shot 1], [Shot 2]) per scene for dynamic focus
echo   - 2-pass quality refinement (preview disabled) for artifact-free character faces
echo   - Lord Voldemort's curse vs. Hermione
echo   - Female Malfoy's heroic shield and transformation into Male Draco
echo   - Draco's outrage: "Turn me back... NOW!"
echo   - Professor McGonagall's holy counter-curse transfiguration
echo   - Female Malfoy's joyful relief and furious "How dare YOU!!!"
echo   - Screen-shattering overpowered finishing spell
echo   - Automatic FFmpeg master stitch with 0.3s audio crossfades
echo.
echo Outputs stored in: \examples_generated\08_malfoid_battle_story\
echo Logs recorded in:   \_project\artifacts\generation_runs_report_v1.0.md
echo ===================================================
echo.

%PYTHON_CMD% scripts\generate_malfoid_story.py
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
echo [SUCCESS] 'Fem Malfoy: Battle of Hogwarts' film completed!
echo Master Film: examples_generated\08_malfoid_battle_story\malfoid_battle_story_master_42s.mp4
echo ===================================================
pause
