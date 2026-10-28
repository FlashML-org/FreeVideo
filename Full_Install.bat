@echo off
setlocal EnableDelayedExpansion

echo ===================================================
echo [FreeVideo] Full Installation & Setup
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

echo [1/3] Checking Python environment...
%PYTHON_CMD% -c "import sys; assert sys.version_info[:2] == (3, 12), f'Python 3.12 is required, found {sys.version}'"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ===================================================
    echo [CRITICAL ERROR] The application crashed!
    echo Please read the traceback above to identify the issue.
    echo ===================================================
    pause
    exit /b %ERRORLEVEL%
)

echo [2/3] Installing launcher & build dependencies...
%PYTHON_CMD% -m pip install -r constraints\windows-launcher.txt
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ===================================================
    echo [CRITICAL ERROR] The application crashed!
    echo Please read the traceback above to identify the issue.
    echo ===================================================
    pause
    exit /b %ERRORLEVEL%
)

for /f "delims=" %%I in ('%PYTHON_CMD% -c "import time, uuid; print(f'dist/installer_{int(time.time())}_{uuid.uuid4().hex[:6]}')"') do set OUT_DIR=%%I

echo [3/3] Building Windows standalone launcher package with target: !OUT_DIR! ...
%PYTHON_CMD% scripts\build_windows.py --out !OUT_DIR!
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
echo [SUCCESS] FreeVideo installer built successfully!
echo Executable artifact: !OUT_DIR!\dist\FreeVideo.exe
echo You can run the executable to install over the current version.
echo ===================================================
pause
