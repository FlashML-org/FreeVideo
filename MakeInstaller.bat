@echo off
setlocal EnableDelayedExpansion

echo ===================================================
echo [FreeVideo] Standalone Installer and Package Builder
echo ===================================================

set "PYTHON_CMD=python"
python -c "import sys; exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if exist "C:\Python312\python.exe" (
        set "PYTHON_CMD=C:\Python312\python.exe"
    ) else (
        echo [ERROR] Python 3.12 is required but not found in PATH or at C:\Python312\python.exe.
        echo Please ensure Python 3.12 is installed.
        pause
        exit /b 1
    )
)

echo.
echo [1/3] Verifying Python 3.12 environment...
%PYTHON_CMD% -c "import sys; assert sys.version_info[:2] == (3, 12), f'Python 3.12 required, found {sys.version}'"
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
echo [2/3] Checking and updating build dependencies...
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

echo.
echo ===================================================
echo           CHOOSE INSTALLER PACKAGE TYPE
echo ===================================================
echo   [1] Windows Standalone Executable (.exe - Recommended)
echo       Builds a single-file FreeVideo.exe installer.
echo.
echo   [2] Windows Portable Folder (.zip archive)
echo       Builds unpacked binaries and FreeVideo-Windows-folder.zip.
echo.
echo   [3] Standalone Full Portable Archive (.zip)
echo       Packages engine, web studio, workflows and scripts into zip.
echo.
echo   [4] macOS / Apple Silicon Package (DMG / .app / zip)
echo       Runs scripts\build_macos.py or packages macOS bundle.
echo.
echo   [5] Linux Standalone Package (.AppImage / zip)
echo       Runs scripts\build_linux.py or packages Linux bundle.
echo.
echo   [0] Exit / Cancel
echo ===================================================
set "TARGET_CHOICE=1"
set /p TARGET_CHOICE="Select an option [1-5, default=1]: "
set "TARGET_CHOICE=%TARGET_CHOICE: =%"

if "%TARGET_CHOICE%"=="0" (
    echo Build cancelled by user.
    exit /b 0
)

set "OUT_DIR=dist\installer_%RANDOM%_%RANDOM%"

if "%TARGET_CHOICE%"=="1" goto BUILD_WIN_EXE
if "%TARGET_CHOICE%"=="2" goto BUILD_WIN_FOLDER
if "%TARGET_CHOICE%"=="3" goto BUILD_ZIP_ALL
if "%TARGET_CHOICE%"=="4" goto BUILD_MACOS
if "%TARGET_CHOICE%"=="5" goto BUILD_LINUX

echo [ERROR] Invalid selection "%TARGET_CHOICE%".
pause
exit /b 1

:BUILD_WIN_EXE
echo.
echo [3/3] Building Windows Standalone Executable in: %OUT_DIR% ...
%PYTHON_CMD% scripts\build_windows.py --out %OUT_DIR%
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
echo [SUCCESS] Windows Standalone Installer built successfully!
echo Executable artifact: %OUT_DIR%\dist\FreeVideo.exe
echo Run this executable to install or update FreeVideo.
echo ===================================================
pause
exit /b 0

:BUILD_WIN_FOLDER
echo.
echo [3/3] Building Windows Portable Folder in: %OUT_DIR% ...
%PYTHON_CMD% scripts\build_windows.py --onefolder --out %OUT_DIR%
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
echo [SUCCESS] Windows Portable Folder built successfully!
echo Folder:   %OUT_DIR%\dist\FreeVideo\
echo Zip File: %OUT_DIR%\dist\FreeVideo-Windows-folder.zip
echo ===================================================
pause
exit /b 0

:BUILD_ZIP_ALL
echo.
echo [3/3] Packaging Full Portable ZIP Archive in: %OUT_DIR% ...
%PYTHON_CMD% scripts\build_zip.py --out %OUT_DIR% --target all
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
echo [SUCCESS] Full Portable ZIP package created successfully!
echo Location: %OUT_DIR%\
echo ===================================================
pause
exit /b 0

:BUILD_MACOS
echo.
echo [3/3] Checking macOS build prerequisites...
%PYTHON_CMD% -c "import platform; exit(0 if platform.system() == 'Darwin' else 1)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Running native macOS build...
    %PYTHON_CMD% scripts\build_macos.py --out %OUT_DIR%
) else (
    echo [INFO] Native macOS DMG or App builds require macOS on Darwin Apple Silicon.
    echo Generating macOS-ready portable deployment zip package instead...
    %PYTHON_CMD% scripts\build_zip.py --out %OUT_DIR% --target macos
)
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
echo [SUCCESS] macOS package created successfully!
echo Location: %OUT_DIR%\
echo ===================================================
pause
exit /b 0

:BUILD_LINUX
echo.
echo [3/3] Checking Linux build prerequisites...
%PYTHON_CMD% -c "import platform; exit(0 if platform.system() == 'Linux' else 1)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Running native Linux build...
    %PYTHON_CMD% scripts\build_linux.py --out %OUT_DIR%
) else (
    echo [INFO] Native Linux AppImage builds require a Linux host with binutils.
    echo Generating Linux-ready portable deployment zip package instead...
    %PYTHON_CMD% scripts\build_zip.py --out %OUT_DIR% --target linux
)
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
echo [SUCCESS] Linux package created successfully!
echo Location: %OUT_DIR%\
echo ===================================================
pause
exit /b 0
