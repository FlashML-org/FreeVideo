@echo off
setlocal EnableDelayedExpansion

rem ==============================================================================
rem [FreeVideo] Model and Asset Retrieval Entry Point
rem Last Verified: 2026-10-27
rem Estimated Total Size: 28.5 GB
rem ==============================================================================

echo ===================================================
echo [FreeVideo] Full Model and Asset Downloader & Verifier
echo Last Verified: 2026-10-27
echo Estimated Total Size: 28.5 GB
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

echo [1/2] Verifying upstream Hugging Face model endpoints...
%PYTHON_CMD% scripts\download_models.py --check-only
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
echo [2/2] Model verification complete.
echo You can run the FreeVideo Launcher to automatically download or link existing weights.
echo ===================================================
pause
