@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PWARDS Installer
color 0A

echo(
echo   +============================================================+
echo(  ^|                                                            ^|
echo(  ^|                         PWARDS                             ^|
echo(  ^|   (Pasacao Weather Atmospheric and Real-Time Data System)  ^|
echo   +============================================================+
echo   +============================================================+
echo(

:: SET WORKING DIRECTORY TO WHERE THE BAT FILE IS LOCATED
cd /d "%~dp0"
echo Working directory: %CD%
echo(

set "MIN_PYTHON_MAJOR=3"
set "MIN_PYTHON_MINOR=12"
set "MIN_PYTHON_MICRO=4"
set "PYTHON_DL_VER=3.12.4"
set "PYTHON_EXE=python-%PYTHON_DL_VER%-amd64.exe"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_DL_VER%/%PYTHON_EXE%"
set "PYTHON_DIR=C:\Python312"
set "LOG_FILE=pwards_install.log"
set "PYTHON_JUST_INSTALLED="

>> "%LOG_FILE%" echo [%date% %time%] Starting PWARDS installation from: %CD%

:: Initialize simple progress tracking
set "PROGRESS=0"

call :updateProgress 2 "Checking admin privileges..."
net session >nul 2>&1 || (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Not running as administrator
    call :updateProgress 2 "ERROR: Run as Administrator"
    echo(
    echo Please right-click this file and select "Run as administrator"
    timeout /t 5 >nul
    exit /b 1
)

call :updateProgress 5 "Checking for running Python processes..."
tasklist /fi "imagename eq python.exe" /fo csv 2>nul | findstr /i "python.exe" >nul
if not errorlevel 1 (
    call :updateProgress 5 "WARNING: Python is running."
    echo(
    echo Some Python applications are running.
    echo Press any key to continue...
    pause >nul
)

call :updateProgress 10 "Checking for Python >= %MIN_PYTHON_MAJOR%.%MIN_PYTHON_MINOR%.%MIN_PYTHON_MICRO%..."
set "PYTHON_BIN="
set "PYTHON_FOUND="

:: Check Python in PATH first
for /f "delims=" %%I in ('where python 2^>nul') do (
    call :checkPythonVersion "%%I"
    if defined PYTHON_FOUND goto python_checks_done
)

:: Check common Python installation directories
for %%D in (
    "C:\Python312"
    "C:\Python313" 
    "C:\Python314"
    "C:\Python39"
    "C:\Python310"
    "C:\Python311"
    "%PROGRAMFILES%\Python312"
    "%PROGRAMFILES%\Python313"
    "%PROGRAMFILES%\Python39"
    "%PROGRAMFILES%\Python310"
    "%PROGRAMFILES%\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python313"
) do (
    if exist "%%~D\python.exe" (
        call :checkPythonVersion "%%~D\python.exe"
        if defined PYTHON_FOUND goto python_checks_done
    )
)

:: Check default directory
if exist "%PYTHON_DIR%\python.exe" (
    call :checkPythonVersion "%PYTHON_DIR%\python.exe"
    if defined PYTHON_FOUND goto python_checks_done
)

:python_checks_done
if defined PYTHON_FOUND (
    >> "%LOG_FILE%" echo [%date% %time%] Found Python at: %PYTHON_BIN%
    call :updateProgress 20 "Found Python %PYTHON_VERSION%"
    goto verify_python_path
)

call :updateProgress 30 "Downloading Python %PYTHON_DL_VER%..."
>> "%LOG_FILE%" echo [%date% %time%] Downloading Python from: %PYTHON_URL%

if exist "%PYTHON_EXE%" (
    call :updateProgress 32 "Cleaning up old installer..."
    del "%PYTHON_EXE%" >nul 2>&1
)

call :updateProgress 35 "Downloading (this may take a minute)..."
powershell -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_EXE%' -ErrorAction Stop } catch { Write-Error 'Download failed'; exit 1 }" || (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Python download failed
    call :updateProgress 35 "ERROR: Download failed"
    echo(
    echo Or download manually from:
    echo   %PYTHON_URL%
    echo(
    timeout /t 5 >nul
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Python installer not found after download
    call :updateProgress 40 "ERROR: Installer not found"
    timeout /t 3 >nul
    exit /b 1
)

>> "%LOG_FILE%" echo [%date% %time%] Python installer downloaded successfully: %PYTHON_EXE%

call :updateProgress 50 "Installing Python %PYTHON_DL_VER%..."
echo Installing Python... Please wait...
>> "%LOG_FILE%" echo [%date% %time%] Starting Python installation...

start /wait "" "%PYTHON_EXE%" /quiet InstallAllUsers=1 PrependPath=1 TargetDir="%PYTHON_DIR%" Include_test=0 Include_launcher=1 || (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Python installation failed
    call :updateProgress 50 "ERROR: Installation failed"
    timeout /t 3 >nul
    exit /b 1
)

set "PYTHON_BIN=%PYTHON_DIR%\python.exe"
set "PYTHON_JUST_INSTALLED=1"

call :updateProgress 52 "Cleaning up installer..."
del "%PYTHON_EXE%" >nul 2>&1
>> "%LOG_FILE%" echo [%date% %time%] Python installed to: %PYTHON_DIR%

:verify_python_path
call :updateProgress 60 "Verifying Python installation..."
if not exist "%PYTHON_BIN%" (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Python binary not found: %PYTHON_BIN%
    call :updateProgress 60 "ERROR: Python binary not found"
    timeout /t 3 >nul
    exit /b 1
)

:: Final version check
call :checkPythonVersion "%PYTHON_BIN%"
if not defined PYTHON_FOUND (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Python version requirement not met
    call :updateProgress 65 "ERROR: Python version requirement not met"
    echo(
    echo Required: %MIN_PYTHON_MAJOR%.%MIN_PYTHON_MINOR%.%MIN_PYTHON_MICRO% or higher
    timeout /t 5 >nul
    exit /b 1
)

>> "%LOG_FILE%" echo [%date% %time%] Python verified: %PYTHON_VERSION%
call :updateProgress 70 "Python %PYTHON_VERSION% verified ✓"

:: Add Python to PATH temporarily if needed
call :updateProgress 72 "Verifying system PATH..."
echo %PATH% | find /i "%PYTHON_DIR%" >nul
if errorlevel 1 (
    call :updateProgress 73 "Adding Python to PATH temporarily..."
    set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%"
)

:: Check for and install requirements.txt
call :updateProgress 75 "Checking for dependencies..."
if exist "requirements.txt" (
    call :updateProgress 76 "Found requirements.txt"
    call :updateProgress 77 "Installing Python packages..."
    >> "%LOG_FILE%" echo [%date% %time%] Installing requirements from requirements.txt
    
    "%PYTHON_BIN%" -m pip install --upgrade pip >nul 2>&1
    if errorlevel 1 (
        call :updateProgress 78 "WARNING: Some packages failed to install"
        >> "%LOG_FILE%" echo [%date% %time%] WARNING: pip install had errors
    ) else (
        call :updateProgress 78 "Dependencies installed ✓"
        >> "%LOG_FILE%" echo [%date% %time%] Requirements installed successfully
    )
) else (
    call :updateProgress 78 "No requirements.txt found"
    >> "%LOG_FILE%" echo [%date% %time%] No requirements.txt found
)

:: Check if Monson.py exists - NOW IN THE CORRECT DIRECTORY!
call :updateProgress 80 "Looking for Monson.py..."
if not exist "Monson.py" (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Monson.py not found in %CD%
    call :updateProgress 80 "ERROR: Monson.py not found!"
    echo(
    echo Monson.py should be in the same folder as this installer.
    echo(
    echo Current folder: %CD%
    echo(
    echo Files in this folder:
    dir /b
    echo(
    timeout /t 10 >nul
    exit /b 1
)

:: Optional restart recommendation
if defined PYTHON_JUST_INSTALLED (
    call :updateProgress 85 "Python successfully installed!"
    echo(
    echo NOTE: For permanent PATH changes, restart your computer or
    echo open a new Command Prompt window.
    echo Press any key to continue...
    pause >nul
)

:: Launch the application - FROM THE CORRECT DIRECTORY!
call :updateProgress 90 "Launching Monson.py from: %CD%"
>> "%LOG_FILE%" echo [%date% %time%] Launching application: "%PYTHON_BIN%" "Monson.py"

echo Launching PWARDS application...
start "" "%PYTHON_BIN%" Monson.py
if errorlevel 1 (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Failed to launch Monson.py
    call :updateProgress 95 "ERROR: Failed to launch application"
    echo(
    echo Failed to start the application.
    echo Try running manually: "%PYTHON_BIN%" Monson.py
    timeout /t 5 >nul
) else (
    call :updateProgress 95 "Application launched successfully ✓"
    >> "%LOG_FILE%" echo [%date% %time%] Application launched successfully
)

call :updateProgress 100 "Installation Complete!"
timeout /t 2 >nul

>> "%LOG_FILE%" echo [%date% %time%] Installation completed successfully
echo(
echo Installation log saved to: %CD%\%LOG_FILE%
timeout /t 3 >nul
exit /b 0

:: -------- Functions --------

:updateProgress
:: Simple progress display
echo [%~1%%] %~2
goto :eof

:checkPythonVersion
:: Args: %1 = Path to python.exe
:: Sets: PYTHON_FOUND=1 and PYTHON_BIN if version is adequate
::        PYTHON_VERSION=actual version string
setlocal EnableDelayedExpansion
set "pyexe=%~1"

:: Try to get Python version
"%pyexe%" --version >nul 2>&1
if errorlevel 1 (
    endlocal
    goto :eof
)

:: Get Python version using python --version
for /f "tokens=2" %%V in ('""%pyexe%" --version 2>&1"') do (
    set "version_string=%%V"
)

:: Parse version components
for /f "tokens=1-3 delims=." %%A in ("!version_string!") do (
    set "actual_major=%%A"
    set "actual_minor=%%B"
    set "actual_micro=%%C"
)

:: If micro version not provided, set to 0
if not defined actual_micro set "actual_micro=0"

>> "%LOG_FILE%" echo [%date% %time%] Checking Python: !pyexe! = !version_string!

:: Compare version (handles 3.12.5, 3.13.0, 3.14.0, etc.)
set "version_ok="
if !actual_major! GTR %MIN_PYTHON_MAJOR% (
    set "version_ok=1"
) else if !actual_major! EQU %MIN_PYTHON_MAJOR% (
    if !actual_minor! GTR %MIN_PYTHON_MINOR% (
        set "version_ok=1"
    ) else if !actual_minor! EQU %MIN_PYTHON_MINOR% (
        if !actual_micro! GEQ %MIN_PYTHON_MICRO% (
            set "version_ok=1"
        )
    )
)

if defined version_ok (
    endlocal & set "PYTHON_FOUND=1" & set "PYTHON_BIN=%~1" & set "PYTHON_VERSION=%version_string%"
) else (
    endlocal
)
goto :eof